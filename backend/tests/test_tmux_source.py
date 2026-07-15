"""Unit tests for the read-only tmux data source (pole backend-tmux).

Every tmux invocation is mocked at the ``subprocess.run`` boundary; no real
tmux server is required. The service must only ever run read-only
subcommands (``list-panes``, ``capture-pane``) and degrade to empty results
when tmux is absent.
"""

import asyncio
import os
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

import pytest

from app.core.config import Settings
from app.services import tmux_source

_T = TypeVar("_T")

_SID_NEW = "aaaa1111bbbb2222"
_SID_OLD = "cccc3333dddd4444"

_LINE_TEMPLATE = (
    "main\t0\tshell\t%0\t0\tzsh\t1\t120\t30\tzsh\t/tmp\n"
    "main\t0\tshell\t%1\t1\tvim\t0\t120\t30\tclaude\t{proj}\n"
    "team\t1\twork\t%5\t0\tnode\t1\t80\t24\tnode\t/nonexistent\n"
)


def _run(coro: Awaitable[_T]) -> _T:
    """Run a coroutine to completion on a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _fake_run(
    list_output: str | None,
    captures: list[list[str] | None] | None = None,
) -> Callable[..., subprocess.CompletedProcess]:
    """Build a ``subprocess.run`` stand-in for list-panes / capture-pane.

    ``list_output=None`` emulates "no tmux server" (exit 1). Each
    ``capture-pane`` call pops the next entry from ``captures``; ``None``
    entries (or exhaustion) emulate a closed pane (exit 1).
    """
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


def _write_session_marker(project_dir: Path, sid: str) -> Path:
    """Create ``.omc/state/sessions/<sid>/session-started.json`` under a project."""
    state_dir = project_dir / ".omc" / "state" / "sessions" / sid
    state_dir.mkdir(parents=True, exist_ok=True)
    marker = state_dir / "session-started.json"
    marker.write_text('{"session_id": "%s"}' % sid, encoding="utf-8")
    return marker


def _settings(poll: float = 0.01, heartbeat: float = 999.0) -> Settings:
    """Build settings with fast polling for deterministic generator tests."""
    return Settings(poll_interval_s=poll, heartbeat_interval_s=heartbeat)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def test_list_sessions_builds_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """list-panes output is grouped into sessions → windows → panes."""
    proj = tmp_path / "proj"
    _write_session_marker(proj, _SID_NEW)
    output = _LINE_TEMPLATE.format(proj=proj)
    monkeypatch.setattr(tmux_source.subprocess, "run", _fake_run(output))

    sessions = tmux_source.list_tmux_sessions()

    assert [s.name for s in sessions] == ["main", "team"]
    window = sessions[0].windows[0]
    assert window.index == 0
    assert window.name == "shell"
    pane_zsh, pane_claude = window.panes
    assert pane_zsh.id == "0"
    assert pane_zsh.active is True
    assert pane_zsh.width == 120
    assert pane_zsh.height == 30
    assert pane_zsh.claude_session_id is None
    assert pane_claude.command == "claude"
    assert pane_claude.claude_session_id == _SID_NEW
    assert sessions[1].windows[0].panes[0].claude_session_id is None


def test_list_sessions_no_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing tmux binary yields an empty list, never an error."""

    def boom(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess:
        raise FileNotFoundError("tmux")

    monkeypatch.setattr(tmux_source.subprocess, "run", boom)
    assert tmux_source.list_tmux_sessions() == []


def test_list_sessions_no_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-zero tmux exit (no server) yields an empty list."""
    monkeypatch.setattr(tmux_source.subprocess, "run", _fake_run(None))
    assert tmux_source.list_tmux_sessions() == []


def test_list_sessions_skips_malformed_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed list-panes lines are skipped, valid ones kept."""
    output = "garbage-without-tabs\nmain\t0\tshell\t%0\t0\tt\t1\t80\t24\tzsh\t/tmp\n"
    monkeypatch.setattr(tmux_source.subprocess, "run", _fake_run(output))
    sessions = tmux_source.list_tmux_sessions()
    assert len(sessions) == 1
    assert sessions[0].windows[0].panes[0].id == "0"


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------

def test_correlate_picks_latest_session(tmp_path: Path) -> None:
    """With several session markers, the most recently touched sid wins."""
    old_marker = _write_session_marker(tmp_path, _SID_OLD)
    _write_session_marker(tmp_path, _SID_NEW)
    stat = old_marker.stat()
    os.utime(old_marker, (stat.st_atime - 3600, stat.st_mtime - 3600))
    assert tmux_source.correlate_pane("claude", str(tmp_path)) == _SID_NEW


def test_correlate_none_cases(tmp_path: Path) -> None:
    """Non-Claude commands, missing state and missing cwd all yield None."""
    _write_session_marker(tmp_path, _SID_NEW)
    assert tmux_source.correlate_pane("zsh", str(tmp_path)) is None
    assert tmux_source.correlate_pane("claude", str(tmp_path / "void")) is None


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

def test_capture_pane_returns_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    """capture-pane output is split into lines."""
    monkeypatch.setattr(
        tmux_source.subprocess, "run", _fake_run("", [["hello", "world"]])
    )
    assert tmux_source.capture_pane("3") == ["hello", "world"]


def test_capture_pane_gone_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A closed pane (tmux exit 1) yields None."""
    monkeypatch.setattr(tmux_source.subprocess, "run", _fake_run("", [None]))
    assert tmux_source.capture_pane("3") is None


def test_capture_pane_rejects_non_digit_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-digit pane id is refused before any subprocess call (CWE-78)."""

    def forbidden(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess:
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(tmux_source.subprocess, "run", forbidden)
    assert tmux_source.capture_pane("3; rm -rf /") is None
    assert tmux_source.capture_pane("../etc") is None


# ---------------------------------------------------------------------------
# SSE generator
# ---------------------------------------------------------------------------

async def _collect(gen: object) -> list[str]:
    """Drain an async generator into a list."""
    return [message async for message in gen]


def test_generator_snapshot_delta_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stream emits snapshot, then a delta on change, then pane_closed."""
    captures: list[list[str] | None] = [["a"], ["a"], ["a", "b"], None]
    monkeypatch.setattr(tmux_source.subprocess, "run", _fake_run("", captures))

    messages = _run(_collect(tmux_source.pane_event_generator("3", _settings())))

    assert messages[0].startswith("event: snapshot\n")
    assert '"cursor":1' in messages[0]
    assert messages[1].startswith("event: delta\n")
    assert '"lines":["a","b"]' in messages[1]
    assert messages[2].startswith("event: pane_closed\n")
    assert len(messages) == 3


def test_generator_heartbeat_on_silence(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unchanged pane emits a heartbeat once the interval elapses."""
    captures: list[list[str] | None] = [["a"], ["a"], None]
    monkeypatch.setattr(tmux_source.subprocess, "run", _fake_run("", captures))

    settings = _settings(heartbeat=0.0)
    messages = _run(_collect(tmux_source.pane_event_generator("3", settings)))

    assert messages[0].startswith("event: snapshot\n")
    assert messages[1].startswith("event: heartbeat\n")
    assert messages[2].startswith("event: pane_closed\n")


def test_generator_closed_pane_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pane already gone at connect time yields only pane_closed."""
    monkeypatch.setattr(tmux_source.subprocess, "run", _fake_run("", [None]))
    messages = _run(_collect(tmux_source.pane_event_generator("3", _settings())))
    assert len(messages) == 1
    assert messages[0].startswith("event: pane_closed\n")
