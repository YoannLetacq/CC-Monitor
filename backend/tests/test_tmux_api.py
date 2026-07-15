"""Integration tests for the tmux endpoints (pole backend-tmux).

Routes under test (API CONTRACT, brief):
    GET /api/tmux/sessions           → nested session/window/pane tree
    GET /api/tmux/panes/{id}/events  → SSE snapshot/delta/heartbeat/pane_closed

tmux is mocked at the ``subprocess.run`` boundary inside the service module;
the closed-pane capture sequence makes the SSE stream finite so the
synchronous ``TestClient`` can consume it fully.
"""

import subprocess
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.services import tmux_source

_LIST_OUTPUT = "main\t0\tshell\t%3\t0\ttitle\t1\t120\t30\tzsh\t/tmp\t4242\n"


def _fake_run(
    list_output: str | None,
    captures: list[list[str] | None] | None = None,
) -> Callable[..., subprocess.CompletedProcess]:
    """Build a ``subprocess.run`` stand-in (same protocol as the unit tests)."""
    remaining = list(captures or [])

    def run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        if "list-panes" in cmd:
            if list_output is None:
                return subprocess.CompletedProcess(cmd, 1, "", "no server running")
            return subprocess.CompletedProcess(cmd, 0, list_output, "")
        if "capture-pane" in cmd:
            nxt = remaining.pop(0) if remaining else None
            if nxt is None:
                return subprocess.CompletedProcess(cmd, 1, "", "can't find pane")
            return subprocess.CompletedProcess(cmd, 0, "\n".join(nxt) + "\n", "")
        return subprocess.CompletedProcess(cmd, 1, "", "unexpected subcommand")

    return run


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Return a TestClient over a freshly built app with fast polling."""
    from app.core.config import get_settings

    monkeypatch.setenv("POLL_INTERVAL_S", "0.01")
    get_settings.cache_clear()

    from app.main import create_app

    built = TestClient(create_app())
    yield built
    get_settings.cache_clear()


def test_sessions_lists_panes_camel_case(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/tmux/sessions returns the nested tree with camelCase keys."""
    monkeypatch.setattr(tmux_source.subprocess, "run", _fake_run(_LIST_OUTPUT))
    resp = client.get("/api/tmux/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["name"] == "main"
    pane = body[0]["windows"][0]["panes"][0]
    assert pane["id"] == "3"
    assert pane["active"] is True
    assert "claudeSessionId" in pane


def test_sessions_empty_without_tmux(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a tmux server the endpoint returns 200 with an empty list."""
    monkeypatch.setattr(tmux_source.subprocess, "run", _fake_run(None))
    resp = client.get("/api/tmux/sessions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_events_404_unknown_pane(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An id absent from the enumerated pane list yields 404."""
    monkeypatch.setattr(tmux_source.subprocess, "run", _fake_run(_LIST_OUTPUT))
    resp = client.get("/api/tmux/panes/9/events")
    assert resp.status_code == 404


def test_events_404_non_digit_pane_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-digit pane id is refused with 404 (no subprocess reachable)."""
    monkeypatch.setattr(tmux_source.subprocess, "run", _fake_run(_LIST_OUTPUT))
    resp = client.get("/api/tmux/panes/not-a-pane/events")
    assert resp.status_code == 404


def test_events_404_without_tmux(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without tmux there are no panes, so any pane id yields 404."""
    monkeypatch.setattr(tmux_source.subprocess, "run", _fake_run(None))
    resp = client.get("/api/tmux/panes/3/events")
    assert resp.status_code == 404


def test_events_streams_snapshot_then_closed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live pane streams a snapshot, then pane_closed once it dies."""
    monkeypatch.setattr(
        tmux_source.subprocess,
        "run",
        _fake_run(_LIST_OUTPUT, [["hello pole"], None]),
    )
    resp = client.get("/api/tmux/panes/3/events")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert "event: snapshot" in resp.text
    assert "hello pole" in resp.text
    assert "event: pane_closed" in resp.text


def test_health_not_regressed(client: TestClient) -> None:
    """GET /health still returns 200 with {status: ok}."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
