"""Integration tests for the SSE events endpoint (W4, Increment 3).

Route under test (§1.1):
    GET /api/sessions/{sid}/events → text/event-stream

Coverage targets (§6 TDD brief, case 8):
    - 404 on unknown sid
    - 409 on a known but non-active session
    - 200 + content-type text/event-stream on an active session
    - /health and Incr.2 routes not regressed

Fixtures use tmp_path (anonymized); the real ~/.claude is never read.
``CLAUDE_PROJECTS_ROOT`` is injected via env override.
"""

import asyncio
import json
from collections.abc import Awaitable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypeVar

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_T = TypeVar("_T")


def _run(coro: Awaitable[_T]) -> _T:
    """Run a coroutine to completion on a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _drive_sse(app: FastAPI, path: str) -> tuple[dict, list[bytes]]:
    """Drive an SSE route over raw ASGI, disconnecting after the first chunk.

    Returns ``(start_message, body_chunks)``.  A real ``http.disconnect`` is
    delivered once the first body chunk arrives, so the generator's
    ``finally`` runs and the watchdog observer is torn down — exercising the
    disconnect path that the synchronous ``TestClient`` cannot drive on an
    infinite stream.
    """
    sent: list[dict] = []
    chunks: list[bytes] = []
    disconnect = asyncio.Event()

    async def receive() -> dict:
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)
        if message["type"] == "http.response.body":
            body = message.get("body", b"")
            if body:
                chunks.append(body)
                disconnect.set()

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=5.0)
    start = next(m for m in sent if m["type"] == "http.response.start")
    return start, chunks

_ACTIVE_SID = "aaaa1111bbbb2222"
_STALE_SID = "cccc3333dddd4444"
_SLUG_NAME = "events-project"

_TRACKING = {
    "agents": [
        {
            "agent_id": "run111aaa222bbb3",
            "agent_type": "executor",
            "started_at": "2026-06-19T09:00:00.000Z",
            "status": "running",
            "parent_mode": "none",
        }
    ],
    "total_spawned": 1,
    "last_updated": "2026-06-19T09:00:00.000Z",
}


def _now_iso(delta_s: int = 0) -> str:
    """Return an ISO-8601 timestamp offset from now by ``delta_s`` seconds."""
    moment = datetime.now(timezone.utc) + timedelta(seconds=delta_s)
    return moment.isoformat().replace("+00:00", "Z")


def _write_transcript(projects_root: Path, project_dir: Path, sid: str, ts_iso: str) -> None:
    """Write a minimal transcript whose last activity is ``ts_iso``."""
    slug = str(project_dir).replace("/", "-")
    transcript = projects_root / slug / f"{sid}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    with open(transcript, "w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"type": "user", "cwd": str(project_dir), "timestamp": ts_iso}
            )
            + "\n"
        )


def _write_tracking(project_dir: Path) -> None:
    """Write the running-agent tracking file for the project."""
    state_dir = project_dir / ".omc" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / "subagent-tracking.json", "w", encoding="utf-8") as handle:
        json.dump(_TRACKING, handle)
    (project_dir / ".omc" / "reports").mkdir(parents=True, exist_ok=True)


@pytest.fixture()
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Build a project tree with an active and a stale session; return the app."""
    from app.core.config import get_settings

    projects_root = tmp_path / "claude_projects"
    project_dir = tmp_path / "work" / _SLUG_NAME
    project_dir.mkdir(parents=True, exist_ok=True)

    _write_transcript(projects_root, project_dir, _ACTIVE_SID, _now_iso(0))
    _write_transcript(projects_root, project_dir, _STALE_SID, "2020-01-01T00:00:00.000Z")
    _write_tracking(project_dir)

    monkeypatch.setenv("CLAUDE_PROJECTS_ROOT", str(projects_root))
    get_settings.cache_clear()

    from app.main import create_app

    built = create_app()
    yield built
    get_settings.cache_clear()


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    """Return a synchronous TestClient bound to the built app."""
    return TestClient(app)


def test_events_404_unknown_sid(client: TestClient) -> None:
    """GET /api/sessions/{unknown}/events returns 404."""
    resp = client.get("/api/sessions/unknown-session-id/events")
    assert resp.status_code == 404


def test_events_409_not_active(client: TestClient) -> None:
    """A known but stale session yields 409 'Session not live'."""
    resp = client.get(f"/api/sessions/{_STALE_SID}/events")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Session not live"


def test_events_path_traversal_blocked(client: TestClient) -> None:
    """Path-traversal in sid is blocked (404/422, never a directory escape)."""
    resp = client.get("/api/sessions/../../../etc/passwd/events")
    assert resp.status_code in (404, 422)


def _header(start: dict, name: str) -> str:
    """Return a decoded response header value from an ASGI start message."""
    wanted = name.lower().encode()
    for key, value in start["headers"]:
        if key.lower() == wanted:
            return value.decode()
    return ""


def test_events_200_stream_active(app: FastAPI) -> None:
    """An active session yields 200 text/event-stream and a tree snapshot.

    Driven over raw ASGI so the disconnect after the first chunk propagates a
    real ``http.disconnect`` into the generator's ``finally`` (clean observer
    teardown).  The synchronous ``TestClient`` cannot tear down an infinite SSE
    body, and ``httpx.ASGITransport`` buffers rather than streams it.
    """
    start, chunks = _run(_drive_sse(app, f"/api/sessions/{_ACTIVE_SID}/events"))
    assert start["status"] == 200
    assert _header(start, "content-type").startswith("text/event-stream")
    assert chunks[0].startswith(b"event: tree")
    assert _ACTIVE_SID.encode() in chunks[0]


def test_health_not_regressed(client: TestClient) -> None:
    """GET /health still returns 200 with {status: ok}."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_orchestration_route_not_regressed(client: TestClient) -> None:
    """An Incr.2 route (/agents) still returns 200 for the active session."""
    resp = client.get(f"/api/sessions/{_ACTIVE_SID}/agents")
    assert resp.status_code == 200
    assert resp.json()["sessionId"] == _ACTIVE_SID
