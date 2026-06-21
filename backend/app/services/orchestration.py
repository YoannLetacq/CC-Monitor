"""Read-only parsers for orchestration state and timeline (F1/F2).

This module reads two on-disk sources from a resolved project's
``.omc/state`` directory and turns them into the F1/F2 response schemas:

* ``subagent-tracking.json`` — the authoritative source of agent topology,
  statuses and durations. On real data every agent carries
  ``parent_mode="none"`` (no parent/child link), so F1 is a flat list ordered
  by ``started_at`` (``hierarchy_available=False``); the ``total_*`` aggregates
  are incoherent and are never trusted.
* ``agent-replay-*.jsonl`` — a weak secondary signal. Every line carries
  ``t=0`` (unusable for chronology) and ``agent_stop`` events are massively
  duplicated; they are deduplicated per short ``agent`` prefix keeping the
  maximum ``duration_ms``. The replay only confirms ``agent_start`` and
  enriches ``success`` via prefix matching against full tracking ids.

All file access is read-only and defensive: a missing or corrupt file yields
empty results rather than raising, and corrupt JSON lines are skipped.
"""

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.schemas.orchestration import AgentNode, AgentTree, TimelineEvent
from app.services.discovery import _iter_json_lines, _parse_timestamp

logger = logging.getLogger(__name__)

_TRACKING_FILE: str = "subagent-tracking.json"
_REPLAY_GLOB: str = "agent-replay-*.jsonl"
_STOP_EVENTS: frozenset[str] = frozenset({"agent_stop", "agent_complete"})
_KNOWN_EVENTS: frozenset[str] = frozenset(
    {"agent_start", "agent_stop", "agent_complete"}
)
_KNOWN_STATUSES: frozenset[str] = frozenset(
    {"running", "completed", "failed", "blocked"}
)


@dataclass(frozen=True)
class AgentNodeData:
    """Normalized, raw tracking facts for a single agent (no recomputation).

    ``agent_type`` is normalized (plugin prefix stripped). ``duration_ms`` is
    the tracking value when finished and ``None`` while running (the API
    computes the elapsed time).
    """

    agent_id: str
    agent_type: str
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None


@dataclass(frozen=True)
class ReplayEventData:
    """A deduplicated replay event keyed by the short ``agent`` prefix.

    The ``t`` field is intentionally never read (always ``0`` on real data).
    """

    agent: str
    agent_type: str
    event: str
    success: bool | None
    duration_ms: int | None


def normalize_agent_type(raw: str) -> str:
    """Strip a leading ``"<plugin>:"`` prefix from an agent type.

    Idempotent: a bare type without a prefix is returned unchanged.
    """
    _, _, tail = raw.partition(":")
    return tail if tail else raw


def derive_role(agent_type: str) -> str:
    """Derive a coarse role from a normalized ``agent_type`` (pure, §1.2).

    Order matters: ``lead`` and ``verifier`` win over the worker default;
    explicit orchestrator names map to ``orchestrator``; the replay sentinel
    ``"unknown"`` stays ``unknown``; everything else defaults to ``worker``.
    """
    lowered: str = agent_type.lower()
    if lowered == "unknown":
        return "unknown"
    if "lead" in lowered:
        return "lead"
    if "verifier" in lowered:
        return "verifier"
    if lowered in {"orchestrator", "principal", "main"}:
        return "orchestrator"
    return "worker"


def _coerce_status(raw: object) -> str:
    """Map a tracking status to a known value, defaulting to ``unknown``."""
    return raw if isinstance(raw, str) and raw in _KNOWN_STATUSES else "unknown"


def _coerce_int(raw: object) -> int | None:
    """Return ``raw`` as an ``int`` when it is a non-bool integer, else None."""
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def _coerce_dt(raw: object) -> datetime | None:
    """Parse an ISO-8601 timestamp value, tolerating non-strings."""
    return _parse_timestamp(raw) if isinstance(raw, str) else None


def _parse_agent(entry: object) -> AgentNodeData | None:
    """Build an :class:`AgentNodeData` from one tracking entry, or ``None``.

    An entry without a string ``agent_id`` is skipped (defensive parsing).
    """
    if not isinstance(entry, dict):
        return None
    agent_id = entry.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id:
        return None
    raw_type = entry.get("agent_type")
    agent_type = normalize_agent_type(raw_type) if isinstance(raw_type, str) else "unknown"
    return AgentNodeData(
        agent_id=agent_id,
        agent_type=agent_type,
        status=_coerce_status(entry.get("status")),
        started_at=_coerce_dt(entry.get("started_at")),
        completed_at=_coerce_dt(entry.get("completed_at")),
        duration_ms=_coerce_int(entry.get("duration_ms")),
    )


def _load_tracking_payload(state_dir: Path) -> dict | None:
    """Read ``subagent-tracking.json``, tolerating absence or corruption."""
    path: Path = state_dir / _TRACKING_FILE
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.debug("no usable tracking at %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def read_tracking(state_dir: Path) -> list[AgentNodeData]:
    """Read agent tracking into normalized data, ignoring ``total_*``.

    A missing or corrupt file yields an empty list; malformed agent entries
    are skipped. No KPI is recomputed here — values are exposed raw/normalized.
    """
    payload = _load_tracking_payload(state_dir)
    if payload is None:
        return []
    agents = payload.get("agents")
    if not isinstance(agents, list):
        return []
    nodes: list[AgentNodeData] = []
    for entry in agents:
        parsed = _parse_agent(entry)
        if parsed is not None:
            nodes.append(parsed)
    return nodes


def compute_batches(
    nodes: Iterable[AgentNodeData],
) -> dict[str, int | None]:
    """Group agents into best-effort temporal batches by interval overlap.

    Agents whose ``[started_at, completed_at]`` windows overlap share a batch
    index (0, 1, …) assigned in start order. A still-running agent extends to
    the latest known end so it stays in the current open batch. An agent
    without ``started_at`` maps to ``None`` (batch unknowable, §1.3).
    """
    started = [n for n in nodes if n.started_at is not None]
    started.sort(key=lambda n: n.started_at)
    batches: dict[str, int | None] = {
        n.agent_id: None for n in nodes if n.started_at is None
    }
    current_batch: int = -1
    current_end: datetime | None = None
    for node in started:
        end = node.completed_at if node.completed_at is not None else node.started_at
        if current_end is None or node.started_at > current_end:
            current_batch += 1
            current_end = end
        elif end > current_end:
            current_end = end
        batches[node.agent_id] = current_batch
    return batches


def _to_agent_node(node: AgentNodeData, batch: int | None) -> AgentNode:
    """Project an :class:`AgentNodeData` plus its batch into an ``AgentNode``."""
    status = node.status if node.status in _KNOWN_STATUSES else "unknown"
    return AgentNode(
        agent_id=node.agent_id,
        agent_type=node.agent_type,
        role=derive_role(node.agent_type),
        status=status,
        started_at=node.started_at,
        completed_at=node.completed_at,
        duration_ms=node.duration_ms,
        batch=batch,
    )


def build_agent_tree(state_dir: Path, session_id: str) -> AgentTree:
    """Build the flat, time-ordered F1 tree for a session.

    Nodes are ordered by ``started_at`` (``None`` last) and tagged with a
    best-effort temporal ``batch``. ``hierarchy_available`` is always ``False``
    because real data carries no parent/child link.
    """
    nodes = read_tracking(state_dir)
    batches = compute_batches(nodes)
    ordered = sorted(
        nodes, key=lambda n: (n.started_at is None, n.started_at or datetime.min)
    )
    agent_nodes = [_to_agent_node(node, batches.get(node.agent_id)) for node in ordered]
    return AgentTree(
        session_id=session_id,
        nodes=agent_nodes,
        hierarchy_available=False,
    )


def _parse_replay_line(obj: object) -> ReplayEventData | None:
    """Build a :class:`ReplayEventData` from one replay line, or ``None``.

    A line without a string ``agent`` is skipped. ``t`` is never read.
    """
    if not isinstance(obj, dict):
        return None
    agent = obj.get("agent")
    if not isinstance(agent, str) or not agent:
        return None
    raw_type = obj.get("agent_type")
    agent_type = normalize_agent_type(raw_type) if isinstance(raw_type, str) else "unknown"
    raw_event = obj.get("event")
    event = raw_event if isinstance(raw_event, str) else "unknown"
    success = obj.get("success")
    return ReplayEventData(
        agent=agent,
        agent_type=agent_type,
        event=event,
        success=success if isinstance(success, bool) else None,
        duration_ms=_coerce_int(obj.get("duration_ms")),
    )


def _keep_max_stop(current: ReplayEventData, candidate: ReplayEventData) -> ReplayEventData:
    """Return the stop event with the larger ``duration_ms`` (None as -1)."""
    current_ms = current.duration_ms if current.duration_ms is not None else -1
    candidate_ms = candidate.duration_ms if candidate.duration_ms is not None else -1
    return candidate if candidate_ms > current_ms else current


def _iter_replay_lines(state_dir: Path) -> Iterable[ReplayEventData]:
    """Yield parsed replay events across all replay files in ``state_dir``."""
    try:
        files = sorted(state_dir.glob(_REPLAY_GLOB))
    except OSError as exc:
        logger.debug("cannot list replay files in %s: %s", state_dir, exc)
        return
    for path in files:
        for obj in _iter_json_lines(path):
            parsed = _parse_replay_line(obj)
            if parsed is not None:
                yield parsed


def read_replay(state_dir: Path) -> list[ReplayEventData]:
    """Read replay events, deduplicating stops per short ``agent`` prefix.

    ``agent_start`` events are kept once per agent (unique starts); stop
    events (``agent_stop``/``agent_complete``) collapse to a single event per
    agent keeping the maximum ``duration_ms``. ``t`` is never read. A missing
    replay file yields an empty list.
    """
    starts: dict[str, ReplayEventData] = {}
    stops: dict[str, ReplayEventData] = {}
    for event in _iter_replay_lines(state_dir):
        if event.event == "agent_start":
            starts.setdefault(event.agent, event)
        elif event.event in _STOP_EVENTS:
            existing = stops.get(event.agent)
            stops[event.agent] = (
                event if existing is None else _keep_max_stop(existing, event)
            )
    return list(starts.values()) + list(stops.values())


def match_full_id(short: str, full_ids: Iterable[str]) -> str | None:
    """Return the full id that ``short`` is a prefix of, else ``None``."""
    for full in full_ids:
        if full.startswith(short):
            return full
    return None


def _stop_index(events: Iterable[ReplayEventData]) -> dict[str, ReplayEventData]:
    """Index deduplicated stop events by their short ``agent`` prefix."""
    return {e.agent: e for e in events if e.event in _STOP_EVENTS}


def _matched_stop(
    agent_id: str,
    stops: dict[str, ReplayEventData],
) -> ReplayEventData | None:
    """Find the replay stop whose short ``agent`` prefixes ``agent_id``."""
    for short, event in stops.items():
        if agent_id.startswith(short):
            return event
    return None


def _coerce_event_kind(raw: str) -> str:
    """Map a raw event string to a known kind, defaulting to ``unknown``."""
    return raw if raw in _KNOWN_EVENTS else "unknown"


def _start_event(node: AgentNodeData) -> TimelineEvent:
    """Build the ``agent_start`` timeline event from tracking (time authority)."""
    return TimelineEvent(
        agent_id=node.agent_id,
        agent_type=node.agent_type,
        event="agent_start",
        success=None,
        at=node.started_at,
        duration_ms=None,
    )


def _stop_event(
    node: AgentNodeData,
    stop: ReplayEventData | None,
) -> TimelineEvent:
    """Build the ``agent_stop`` timeline event, enriching success from replay."""
    return TimelineEvent(
        agent_id=node.agent_id,
        agent_type=node.agent_type,
        event="agent_stop",
        success=stop.success if stop is not None else None,
        at=node.completed_at,
        duration_ms=node.duration_ms,
    )


def build_timeline(state_dir: Path, session_id: str) -> list[TimelineEvent]:
    """Build the F2 timeline from tracking time, enriched by replay success.

    Chronology derives strictly from tracking ``started_at`` / ``completed_at``
    (the replay ``t`` is always ``0`` and unusable). For each agent a start
    event is emitted, and a stop event whenever the agent has completed; the
    stop's ``success`` is taken from the prefix-matched replay event. Events
    are ordered by their tracking time (``None`` last).
    """
    logger.debug("building timeline for session %s", session_id)
    nodes = read_tracking(state_dir)
    stops = _stop_index(read_replay(state_dir))
    events: list[TimelineEvent] = []
    for node in nodes:
        events.append(_start_event(node))
        if node.completed_at is not None or node.status in {"completed", "failed"}:
            events.append(_stop_event(node, _matched_stop(node.agent_id, stops)))
    events.sort(key=lambda e: (e.at is None, e.at or datetime.min))
    return events
