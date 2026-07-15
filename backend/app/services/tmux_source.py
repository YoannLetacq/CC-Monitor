"""Read-only tmux data source: pane discovery, live capture, correlation.

Backs the ``/api/tmux`` routes (brief, scope 1):

* :func:`list_tmux_sessions` — one ``tmux list-panes -a -F …`` call parsed
  into the nested session → window → pane tree;
* :func:`pane_event_generator` — an SSE generator polling
  ``capture-pane -p`` and emitting ``snapshot`` / ``delta`` (full screen,
  replace semantics) / ``heartbeat`` / ``pane_closed``;
* :func:`correlate_pane` — best-effort, nullable pane → Claude session id.

Security (AGENT_CONDUCT §1.4): every tmux invocation is a **read-only**
subcommand (``list-panes``, ``capture-pane``) executed with a list argv —
never a shell string (CWE-78). Pane ids are validated as digits before being
formatted into a ``%<id>`` target. A missing tmux binary or server degrades
to an empty result, never a 500 (graceful no-tmux).
"""

import asyncio
import json
import logging
import os
import subprocess
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import Settings
from app.schemas.events import encode_sse
from app.schemas.tmux import (
    PaneClosedEvent,
    PaneDeltaEvent,
    PaneHeartbeatEvent,
    PaneSnapshotEvent,
    TmuxPane,
    TmuxSession,
    TmuxWindow,
)

logger = logging.getLogger(__name__)

_FIELD_SEP = "\t"
_LIST_FORMAT = _FIELD_SEP.join(
    (
        "#{session_name}",
        "#{window_index}",
        "#{window_name}",
        "#{pane_id}",
        "#{pane_index}",
        "#{pane_title}",
        "#{pane_active}",
        "#{pane_width}",
        "#{pane_height}",
        "#{pane_current_command}",
        "#{pane_current_path}",
        "#{pane_pid}",
    )
)
_TMUX_TIMEOUT_S = 5.0
_CLAUDE_COMMANDS = frozenset({"claude", "node"})
_PROC_ROOT = Path("/proc")
_CLK_TCK = os.sysconf("SC_CLK_TCK")
_START_SLACK_S = 5.0


def _run_tmux(args: list[str]) -> str | None:
    """Run a read-only tmux subcommand; ``None`` when tmux is unavailable.

    ``None`` covers every degraded case the same way: missing binary
    (``FileNotFoundError``/``OSError``), a hung server (timeout) and a
    non-zero exit (no server, unknown pane).
    """
    try:
        proc = subprocess.run(
            ["tmux", *args],
            capture_output=True,
            text=True,
            timeout=_TMUX_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("tmux unavailable: %s", exc)
        return None
    if proc.returncode != 0:
        logger.debug("tmux exited %s: %s", proc.returncode, proc.stderr.strip())
        return None
    return proc.stdout


def _pid_descendants(root_pid: int) -> set[int]:
    """Return ``root_pid`` plus every descendant pid, read-only via ``/proc``.

    Breadth-first walk over ``/proc/<pid>/task/<tid>/children``; any
    unreadable branch (process gone, permission) is pruned silently so the
    walk never raises.
    """
    seen: set[int] = set()
    stack = [root_pid]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        try:
            task_dirs = list((_PROC_ROOT / str(pid) / "task").iterdir())
        except OSError:
            continue
        for task_dir in task_dirs:
            try:
                children = (task_dir / "children").read_text(encoding="ascii")
            except OSError:
                continue
            stack.extend(int(child) for child in children.split())
    return seen


def _marker_identity(marker: Path) -> tuple[int | None, float | None]:
    """Read ``(recorded pid, started_at epoch)`` from a session marker.

    Both fields are optional in practice; any read/parse failure degrades to
    ``(None, None)`` so correlation falls back instead of raising.
    """
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    pid = payload.get("pid")
    started: float | None = None
    raw_started = payload.get("started_at")
    if isinstance(raw_started, str):
        try:
            moment = datetime.fromisoformat(raw_started)
        except ValueError:
            moment = None
        if moment is not None:
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            started = moment.timestamp()
    return (pid if isinstance(pid, int) else None), started


def _proc_start_time(pid: int) -> float | None:
    """Wall-clock start time of ``pid`` via ``/proc``; ``None`` when unreadable."""
    try:
        proc_stat = (_PROC_ROOT / "stat").read_text(encoding="ascii")
        pid_stat = (_PROC_ROOT / str(pid) / "stat").read_text(encoding="ascii")
    except OSError:
        return None
    boot: float | None = None
    fields = pid_stat.rpartition(")")[2].split()
    try:
        for line in proc_stat.splitlines():
            if line.startswith("btime "):
                boot = float(line.split()[1])
                break
        jiffies = int(fields[19])
    except (IndexError, ValueError):
        return None
    if boot is None:
        return None
    return boot + jiffies / _CLK_TCK


def _claude_start_time(pids: set[int]) -> float | None:
    """Start time of the first Claude-ish process among ``pids``, if any."""
    for pid in sorted(pids):
        try:
            comm = (_PROC_ROOT / str(pid) / "comm").read_text(encoding="ascii")
        except OSError:
            continue
        if comm.strip() in _CLAUDE_COMMANDS:
            return _proc_start_time(pid)
    return None


def correlate_pane(command: str, cwd: str, pane_pid: int | None = None) -> str | None:
    """Best-effort pane → Claude session id; nullable, never raising.

    Pid-tree matching (QA F1, shared-cwd workers): walk the pane pid's
    descendants and prefer (1) the session whose recorded ``pid`` is a live
    descendant, else (2) the session whose ``started_at`` is closest
    at-or-after the claude descendant's process start time — markers record
    the short-lived SessionStart-hook pid, so (1) rarely fires today.
    Fallback (3): newest ``session-started.json`` under the pane's cwd
    (``.omc/state/sessions/<sid>/``), the original heuristic.
    """
    if command not in _CLAUDE_COMMANDS:
        return None
    sessions_dir = Path(cwd) / ".omc" / "state" / "sessions"
    try:
        entries = list(sessions_dir.iterdir())
    except OSError:
        return None
    candidates: list[tuple[float, str]] = []
    identities: dict[str, tuple[int | None, float | None]] = {}
    for entry in entries:
        marker = entry / "session-started.json"
        try:
            mtime = marker.stat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, entry.name))
        identities[entry.name] = _marker_identity(marker)
    if not candidates:
        return None
    if pane_pid is not None:
        descendants = _pid_descendants(pane_pid)
        exact = [c for c in candidates if identities[c[1]][0] in descendants]
        if exact:
            return max(exact)[1]
        start = _claude_start_time(descendants)
        if start is not None:
            timed = sorted(
                (started - start, sid)
                for _, sid in candidates
                if (started := identities[sid][1]) is not None
                and started - start >= -_START_SLACK_S
            )
            if timed:
                return timed[0][1]
    return max(candidates)[1]


def _parse_pane(fields: list[str]) -> tuple[str, int, str, TmuxPane] | None:
    """Parse one list-panes line into ``(session, window_index, window_name, pane)``.

    Malformed lines (wrong field count, non-numeric ints) yield ``None`` and
    are skipped by the caller.
    """
    try:
        (
            session_name,
            window_raw,
            window_name,
            pane_id,
            pane_index,
            title,
            active,
            width,
            height,
            command,
            cwd,
            pane_pid,
        ) = fields
        window_index = int(window_raw)
        pane = TmuxPane(
            id=pane_id.lstrip("%"),
            index=int(pane_index),
            title=title,
            active=active == "1",
            width=int(width),
            height=int(height),
            command=command,
            claude_session_id=correlate_pane(command, cwd, int(pane_pid)),
        )
    except ValueError:
        logger.debug("tmux: skipping malformed list-panes line: %r", fields)
        return None
    return session_name, window_index, window_name, pane


def list_tmux_sessions() -> list[TmuxSession]:
    """Discover the session → window → pane tree; ``[]`` without tmux."""
    output = _run_tmux(["list-panes", "-a", "-F", _LIST_FORMAT])
    if output is None:
        return []
    sessions: dict[str, dict[int, TmuxWindow]] = {}
    for line in output.splitlines():
        parsed = _parse_pane(line.split(_FIELD_SEP))
        if parsed is None:
            continue
        session_name, window_index, window_name, pane = parsed
        windows = sessions.setdefault(session_name, {})
        window = windows.get(window_index)
        if window is None:
            window = TmuxWindow(index=window_index, name=window_name, panes=[])
            windows[window_index] = window
        window.panes.append(pane)
    return [
        TmuxSession(name=name, windows=[windows[i] for i in sorted(windows)])
        for name, windows in sessions.items()
    ]


def pane_exists(pane_id: str) -> bool:
    """Return whether ``pane_id`` (digits, no ``%``) is a currently listed pane."""
    return any(
        pane.id == pane_id
        for session in list_tmux_sessions()
        for window in session.windows
        for pane in window.panes
    )


def capture_pane(pane_id: str) -> list[str] | None:
    """Capture a pane's visible content as lines; ``None`` when gone/invalid.

    The digits check guards the ``%<id>`` target formatting so no caller
    input ever reaches tmux unvalidated (CWE-78); plain-text capture (no
    ``-e``) keeps the payload free of escape sequences.
    """
    if not pane_id.isdigit():
        return None
    output = _run_tmux(["capture-pane", "-p", "-t", f"%{pane_id}"])
    if output is None:
        return None
    return output.splitlines()


async def pane_event_generator(
    pane_id: str,
    settings: Settings,
) -> AsyncGenerator[str, None]:
    """Yield SSE messages for one pane: snapshot, deltas, heartbeat, closed.

    Pure polling — pane content has no filesystem to watch — reusing the
    existing ``poll_interval_s`` / ``heartbeat_interval_s`` settings. Each
    capture is offloaded to a thread (``asyncio.to_thread``) so the event
    loop never blocks on the subprocess. A ``delta`` carries the FULL
    current screen (replace semantics); the stream ends after
    ``pane_closed``.
    """
    lines = await asyncio.to_thread(capture_pane, pane_id)
    if lines is None:
        yield encode_sse("pane_closed", PaneClosedEvent(pane_id=pane_id))
        return
    yield encode_sse(
        "snapshot",
        PaneSnapshotEvent(pane_id=pane_id, lines=lines, cursor=len(lines)),
    )
    loop = asyncio.get_running_loop()
    last_emit = loop.time()
    while True:
        await asyncio.sleep(settings.poll_interval_s)
        current = await asyncio.to_thread(capture_pane, pane_id)
        if current is None:
            yield encode_sse("pane_closed", PaneClosedEvent(pane_id=pane_id))
            return
        if current != lines:
            lines = current
            yield encode_sse("delta", PaneDeltaEvent(pane_id=pane_id, lines=lines))
            last_emit = loop.time()
        elif loop.time() - last_emit >= settings.heartbeat_interval_s:
            heartbeat = PaneHeartbeatEvent(
                pane_id=pane_id, ts=datetime.now(timezone.utc)
            )
            yield encode_sse("heartbeat", heartbeat)
            last_emit = loop.time()
