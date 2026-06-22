"""SSE streamer: watchdog + polling fallback + snapshot + deltas (W4, Increment 3).

Assembles the increment-3 building blocks into an async generator that backs
``GET /api/sessions/{sid}/events``:

* an initial **snapshot** (``tree`` / ``timeline`` / ``overview``) so the client
  paints immediately, followed by a first drain of the main transcript;
* a **delta loop** that tails the main transcript and every ``active`` sub-agent
  transcript (``subagents/agent-<id>.jsonl``) into ``agent_blocks`` /
  ``tool_activity`` events, recomputes ``tree`` / ``timeline`` / ``overview`` when
  the project state changes, and emits ``report_available`` for new reports;
* a periodic ``heartbeat`` when nothing else is sent.

A ``watchdog.Observer`` provides low-latency wake-ups; an unconditional
``poll_interval_s`` timeout drains anyway (WSL appends are not always notified,
R4).  On client disconnect (``asyncio.CancelledError``) a ``try/finally`` stops
the observer and releases all per-connection state — no watchdog thread leaks
(R-THREAD).  All file access is read-only; ``sid`` / ``agent_id`` are validated
by membership, never interpolated into a path (CWE-22).
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from app.core.config import Settings
from app.schemas.events import (
    AgentBlocksEvent,
    HeartbeatEvent,
    ReportAvailableEvent,
    TimelineEventBatch,
    ToolActivityEvent,
    encode_sse,
)
from app.services.live_parser import ToolPairer, extract_reasoning
from app.services.orchestration import build_agent_tree, build_timeline, read_tracking
from app.services.overview import build_overview
from app.services.reports import list_reports, role_of
from app.services.tailer import TailState, open_tail, read_new_lines

logger = logging.getLogger(__name__)

_TRACKING_FILE: str = "subagent-tracking.json"
_REPLAY_GLOB: str = "agent-replay-*.jsonl"
_SUBAGENT_GLOB: str = "agent-*.jsonl"


@dataclass
class _Flow:
    """Per-transcript tailing state: byte offset plus a stateful tool pairer.

    One flow exists for the orchestrator transcript (``agent_id=None``) and one
    for each ``active`` sub-agent transcript (``agent_id`` non-``None``).
    """

    tail: TailState
    pairer: ToolPairer
    agent_id: str | None


@dataclass
class _Watched:
    """Resolved read-only paths watched for one streaming connection."""

    transcript_path: Path
    state_dir: Path
    reports_dir: Path
    subagents_dir: Path


@dataclass
class _StreamState:
    """Mutable per-connection bookkeeping for incremental draining.

    ``flows`` is keyed by ``agent_id`` (``None`` for the orchestrator).
    ``state_sig`` is the combined mtime signature of tracking + replay files;
    a change triggers a ``tree`` / ``timeline`` / ``overview`` recompute.
    ``known_reports`` is the set of report file names already announced.
    """

    flows: dict[str | None, _Flow] = field(default_factory=dict)
    state_sig: tuple[float, ...] = ()
    known_reports: set[str] = field(default_factory=set)


@dataclass
class _Context:
    """Per-connection invariants threaded through the drain/loop helpers.

    Bundling these four values keeps helper signatures small (and avoids
    repeating the same argument list on every call).
    """

    state: _StreamState
    watched: _Watched
    settings: Settings
    sid: str


class _WakeHandler(FileSystemEventHandler):
    """Watchdog handler that schedules a wake-up on the event loop thread.

    The handler runs on a watchdog worker thread, so it never touches the
    ``asyncio.Queue`` directly; it hands a sentinel to the loop via
    ``call_soon_threadsafe`` (thread-safe), coalescing naturally because the
    queue is drained to empty on each wake.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, queue: "asyncio.Queue[None]") -> None:
        """Store the target loop and wake queue."""
        super().__init__()
        self._loop = loop
        self._queue = queue

    def on_any_event(self, event: FileSystemEvent) -> None:
        """Schedule a single wake-up sentinel onto the event loop."""
        try:
            self._loop.call_soon_threadsafe(self._wake)
        except RuntimeError as exc:  # loop already closed during shutdown
            logger.debug("_WakeHandler: cannot schedule wake: %s", exc)

    def _wake(self) -> None:
        """Push a wake sentinel, ignoring a full queue (already pending)."""
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            pass


def _resolve_paths(transcript_path: Path, project_path: str, sid: str) -> _Watched:
    """Resolve the read-only paths for one connection from validated inputs.

    ``sid`` is already validated by membership upstream; it is only joined here
    to locate the per-session ``subagents`` directory, never to escape it.
    """
    root = Path(project_path)
    state_dir = root / ".omc" / "state"
    reports_dir = root / ".omc" / "reports"
    subagents_dir = transcript_path.parent / sid / "subagents"
    return _Watched(
        transcript_path=transcript_path,
        state_dir=state_dir,
        reports_dir=reports_dir,
        subagents_dir=subagents_dir,
    )


def _mtime(path: Path) -> float:
    """Return the file mtime, or ``0.0`` when the path is absent/unreadable."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _reports_signature(reports_dir: Path) -> tuple[float, ...]:
    """Compute an mtime signature of all ``*.md`` files in ``reports_dir``.

    Including the reports directory in the state signature ensures that
    ``overview`` is recomputed and re-emitted whenever a report is created
    or modified (``build_overview`` aggregates gates from ``load_all_reports``).
    """
    try:
        md_files = sorted(reports_dir.glob("*.md"))
    except OSError:
        md_files = []
    return tuple(_mtime(path) for path in md_files)


def _state_signature(state_dir: Path, reports_dir: Path) -> tuple[float, ...]:
    """Compute an mtime signature of tracking, replay and report files.

    Covers:
    * ``subagent-tracking.json`` — triggers ``tree`` / ``overview`` recompute.
    * ``agent-replay-*.jsonl``  — triggers ``timeline`` recompute.
    * ``reports/*.md``          — triggers ``overview`` recompute (gate aggregation).
    """
    sig: list[float] = [_mtime(state_dir / _TRACKING_FILE)]
    try:
        replays = sorted(state_dir.glob(_REPLAY_GLOB))
    except OSError:
        replays = []
    sig.extend(_mtime(path) for path in replays)
    sig.extend(_reports_signature(reports_dir))
    return tuple(sig)


def _make_flow(path: Path, agent_id: str | None, settings: Settings) -> _Flow:
    """Create a flow positioned at the bounded tail of ``path``."""
    tail = open_tail(path, settings.transcript_tail_max_bytes)
    pairer = ToolPairer(
        max_pending=settings.max_pending_tools,
        cap=settings.tool_content_cap,
    )
    return _Flow(tail=tail, pairer=pairer, agent_id=agent_id)


def _drain_flow(flow: _Flow, session_id: str) -> list[str]:
    """Drain new lines from one flow into encoded SSE messages.

    Emits an ``agent_blocks`` event when reasoning blocks are found and a
    ``tool_activity`` event when tool calls are paired/emitted; the flow's
    offset and pairer state advance in place.
    """
    records, new_tail = read_new_lines(flow.tail)
    flow.tail = new_tail
    if not records:
        return []

    blocks = [block for line in records for block in extract_reasoning(line)]
    calls = [call for line in records for call in flow.pairer.feed(line)]

    messages: list[str] = []
    if blocks:
        event = AgentBlocksEvent(session_id=session_id, agent_id=flow.agent_id, blocks=blocks)
        messages.append(encode_sse("agent_blocks", event))
    if calls:
        event = ToolActivityEvent(session_id=session_id, agent_id=flow.agent_id, calls=calls)
        messages.append(encode_sse("tool_activity", event))
    return messages


def _active_agent_ids(state_dir: Path) -> list[str]:
    """Return the ids of agents whose tracking status is ``running``."""
    return [node.agent_id for node in read_tracking(state_dir) if node.status == "running"]


def _sync_subagent_flows(
    state: _StreamState,
    watched: _Watched,
    settings: Settings,
    session_id: str,
) -> list[str]:
    """Reconcile sub-agent flows with the current set of ``active`` agents.

    For each agent that is newly ``active`` and has a transcript on disk, a
    new :class:`_Flow` is created (bounded by ``settings.transcript_tail_max_bytes``).

    For each agent whose flow exists but is **no longer active** (status moved
    to ``completed``, ``failed``, etc.), a **final drain** is performed to emit
    any remaining blocks, then the flow is removed so memory does not grow
    without bound (MEDIUM-2 fix).

    The ``agent_id`` is validated by membership in the tracking ``agents[]``
    list (via :func:`_active_agent_ids`) and matched against the enumerated
    ``agent-*.jsonl`` file stems, so no user input ever builds a path.  The
    orchestrator flow (``agent_id=None``) is never removed.

    Returns a list of SSE-encoded messages from the final drains of flows that
    were just deactivated.
    """
    active = set(_active_agent_ids(watched.state_dir))
    messages: list[str] = []

    # Build a stem→path map for all sub-agent transcript files on disk.
    stem_to_path: dict[str, Path] = {}
    try:
        for path in watched.subagents_dir.glob(_SUBAGENT_GLOB):
            stem_to_path[path.stem[len("agent-"):]] = path
    except OSError:
        pass

    # Add flows for newly active agents that have a transcript.
    for agent_id, path in stem_to_path.items():
        if agent_id in active and agent_id not in state.flows:
            state.flows[agent_id] = _make_flow(path, agent_id, settings)

    # Drain and remove flows whose agents are no longer active.
    departed = [
        aid for aid in list(state.flows)
        if aid is not None and aid not in active
    ]
    for agent_id in departed:
        flow = state.flows.pop(agent_id)
        messages.extend(_drain_flow(flow, session_id))
        logger.debug("streamer: removed flow for inactive agent %s", agent_id)

    return messages


def _snapshot_messages(watched: _Watched, sid: str) -> list[str]:
    """Build the initial snapshot: ``tree`` / ``timeline`` / ``overview``."""
    tree = build_agent_tree(watched.state_dir, sid)
    timeline = TimelineEventBatch(
        session_id=sid,
        events=build_timeline(watched.state_dir, sid),
    )
    overview = build_overview(watched.state_dir, watched.reports_dir, sid)
    return [
        encode_sse("tree", tree),
        encode_sse("timeline", timeline),
        encode_sse("overview", overview),
    ]


def _recompute_if_changed(state: _StreamState, watched: _Watched, sid: str) -> list[str]:
    """Re-emit ``tree`` / ``timeline`` / ``overview`` when state files changed."""
    signature = _state_signature(watched.state_dir, watched.reports_dir)
    if signature == state.state_sig:
        return []
    state.state_sig = signature
    return _snapshot_messages(watched, sid)


def _new_report_messages(state: _StreamState, watched: _Watched, sid: str) -> list[str]:
    """Emit a ``report_available`` event for each newly seen report file."""
    messages: list[str] = []
    for name in list_reports(watched.reports_dir):
        if name in state.known_reports:
            continue
        state.known_reports.add(name)
        event = ReportAvailableEvent(session_id=sid, role=role_of(name), file_name=name)
        messages.append(encode_sse("report_available", event))
    return messages


def _drain_all(state: _StreamState, watched: _Watched, settings: Settings, sid: str) -> list[str]:
    """Run one full drain pass across transcripts, state files and reports."""
    messages: list[str] = []
    # Reconcile flows: adds new active agents, drains+removes departed ones.
    messages.extend(_sync_subagent_flows(state, watched, settings, sid))
    for flow in state.flows.values():
        messages.extend(_drain_flow(flow, sid))
    messages.extend(_recompute_if_changed(state, watched, sid))
    messages.extend(_new_report_messages(state, watched, sid))
    return messages


def _start_observer(
    watched: _Watched,
    loop: asyncio.AbstractEventLoop,
    queue: "asyncio.Queue[None]",
) -> BaseObserver:
    """Start a watchdog observer over the transcript, state and reports dirs.

    Missing directories are skipped silently; the polling fallback still drains
    them once they appear.  The returned observer must be stopped/joined by the
    caller's ``finally`` block (R-THREAD).
    """
    handler = _WakeHandler(loop, queue)
    observer = Observer()
    dirs = {
        watched.transcript_path.parent,
        watched.subagents_dir,
        watched.state_dir,
        watched.reports_dir,
    }
    for directory in dirs:
        if directory.is_dir():
            observer.schedule(handler, str(directory), recursive=True)
    observer.start()
    return observer


async def _wait_for_wake(queue: "asyncio.Queue[None]", timeout: float) -> None:
    """Await a wake sentinel up to ``timeout`` seconds, draining duplicates.

    A timeout is the polling fallback (R4): the caller drains regardless.  When
    woken, any further queued sentinels are discarded so a burst coalesces into
    a single drain pass.
    """
    try:
        await asyncio.wait_for(queue.get(), timeout=timeout)
    except asyncio.TimeoutError:
        return
    while not queue.empty():
        queue.get_nowait()


async def event_generator(
    project_path: str,
    sid: str,
    transcript_path: Path,
    settings: Settings,
) -> AsyncGenerator[str, None]:
    """Yield SSE messages for a live session: snapshot, then deltas.

    Emits the initial ``tree`` / ``timeline`` / ``overview`` snapshot, then
    loops draining the main transcript and active sub-agent transcripts,
    recomputing state on change, announcing new reports, and emitting a
    ``heartbeat`` after ``heartbeat_interval_s`` of silence.  A ``try/finally``
    guarantees the watchdog observer is stopped on disconnect (R-THREAD).
    """
    watched = _resolve_paths(transcript_path, project_path, sid)
    state = _StreamState(state_sig=_state_signature(watched.state_dir, watched.reports_dir))
    state.flows[None] = _make_flow(transcript_path, None, settings)
    state.known_reports = set(list_reports(watched.reports_dir))

    loop = asyncio.get_running_loop()
    queue: "asyncio.Queue[None]" = asyncio.Queue(maxsize=1)
    observer = _start_observer(watched, loop, queue)
    ctx = _Context(state=state, watched=watched, settings=settings, sid=sid)
    try:
        for message in _snapshot_messages(watched, sid):
            yield message
        for message in _drain_flow(state.flows[None], sid):
            yield message
        last_emit = loop.time()
        while True:
            await _wait_for_wake(queue, settings.poll_interval_s)
            batch, last_emit = _loop_step(ctx, last_emit, loop)
            for message in batch:
                yield message
    finally:
        observer.stop()
        observer.join()


def _loop_step(
    ctx: _Context,
    last_emit: float,
    loop: asyncio.AbstractEventLoop,
) -> tuple[list[str], float]:
    """Compute the next batch of messages and the updated last-emit clock.

    Drains all sources; if anything was produced it is returned and the clock
    advances.  Otherwise a ``heartbeat`` is emitted once the silence exceeds
    ``heartbeat_interval_s``.  An empty batch leaves ``last_emit`` unchanged.
    """
    messages = _drain_all(ctx.state, ctx.watched, ctx.settings, ctx.sid)
    if messages:
        return messages, loop.time()
    now = loop.time()
    if now - last_emit >= ctx.settings.heartbeat_interval_s:
        heartbeat = HeartbeatEvent(session_id=ctx.sid, ts=datetime.now(timezone.utc))
        return [encode_sse("heartbeat", heartbeat)], now
    return [], last_emit
