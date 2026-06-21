"""Unit tests for the read-only orchestration state/timeline parsers (F1/F2).

Fixtures replicate the real on-disk shapes documented in the dispatch
(``subagent-tracking.json`` with plugin-prefixed agent types, mixed
``completed``/``running`` statuses and incoherent ``total_*`` aggregates;
``agent-replay-*.jsonl`` with ``t=0`` on every line and massively duplicated
``agent_stop`` events) inside ``tmp_path``. The real ``~/.claude`` is never
read.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.schemas.orchestration import AgentTree, TimelineEvent
from app.services.orchestration import (
    build_agent_tree,
    build_timeline,
    compute_batches,
    derive_role,
    match_full_id,
    normalize_agent_type,
    read_replay,
    read_tracking,
)


def _write_json(path: Path, payload: dict) -> None:
    """Write a JSON object to ``path`` (test helper, write mode)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    """Write one JSON object per line to ``path`` (test helper, write mode)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line))
            handle.write("\n")


def _tracking_payload() -> dict:
    """Return a tracking payload replicating §0.1 (prefixed types, statuses)."""
    return {
        "agents": [
            {
                "agent_id": "a735f6a53f86e040d",
                "agent_type": "oh-my-claudecode:planner",
                "started_at": "2026-06-19T17:06:45.922Z",
                "parent_mode": "none",
                "status": "completed",
                "completed_at": "2026-06-19T19:24:47.452Z",
                "duration_ms": 8281530,
            },
            {
                "agent_id": "ae09f7ed8f9924b3c",
                "agent_type": "oh-my-claudecode:planner",
                "started_at": "2026-06-19T20:48:54.199Z",
                "parent_mode": "none",
                "status": "running",
            },
        ],
        "total_spawned": 19,
        "total_completed": 160,
        "total_failed": 0,
        "last_updated": "2026-06-19T20:48:54.302Z",
    }


@pytest.fixture()
def state_dir(tmp_path: Path) -> Path:
    """Return an empty anonymized ``.omc/state`` directory under tmp_path."""
    target = tmp_path / "proj" / ".omc" / "state"
    target.mkdir(parents=True, exist_ok=True)
    return target


# --- 1. normalize_agent_type ------------------------------------------------


def test_normalize_strips_plugin_prefix() -> None:
    """A plugin-prefixed type is reduced to its bare name."""
    assert normalize_agent_type("oh-my-claudecode:planner") == "planner"


def test_normalize_is_idempotent_without_prefix() -> None:
    """A bare type is returned unchanged (idempotent)."""
    assert normalize_agent_type("planner") == "planner"


# --- 2. derive_role ---------------------------------------------------------


def test_derive_role_table() -> None:
    """Role derivation matches the locked §1.2 table."""
    assert derive_role("executors-lead") == "lead"
    assert derive_role("verifier") == "verifier"
    assert derive_role("W3-docker") == "worker"
    assert derive_role("executor") == "worker"
    assert derive_role("orchestrator") == "orchestrator"
    assert derive_role("unknown") == "unknown"


# --- 3. read_tracking (defensive) -------------------------------------------


def test_read_tracking_parses_nodes(state_dir: Path) -> None:
    """Tracking yields one entry per agent with normalized types."""
    _write_json(state_dir / "subagent-tracking.json", _tracking_payload())
    nodes = read_tracking(state_dir)
    assert len(nodes) == 2
    assert {n.agent_type for n in nodes} == {"planner"}


def test_read_tracking_running_has_no_duration(state_dir: Path) -> None:
    """A running agent has no ``completed_at`` and no ``duration_ms``."""
    _write_json(state_dir / "subagent-tracking.json", _tracking_payload())
    nodes = {n.agent_id: n for n in read_tracking(state_dir)}
    running = nodes["ae09f7ed8f9924b3c"]
    assert running.status == "running"
    assert running.completed_at is None
    assert running.duration_ms is None


def test_read_tracking_missing_file_is_empty(state_dir: Path) -> None:
    """An absent tracking file yields an empty list, not an error."""
    assert read_tracking(state_dir) == []


def test_read_tracking_corrupt_file_is_empty(state_dir: Path) -> None:
    """A corrupt tracking file is tolerated and yields an empty list."""
    with open(state_dir / "subagent-tracking.json", "w", encoding="utf-8") as handle:
        handle.write("{not json")
    assert read_tracking(state_dir) == []


def test_read_tracking_skips_malformed_agent(state_dir: Path) -> None:
    """An agent entry missing its id is skipped without crashing."""
    payload = _tracking_payload()
    payload["agents"].append({"agent_type": "oh-my-claudecode:executor"})
    _write_json(state_dir / "subagent-tracking.json", payload)
    assert len(read_tracking(state_dir)) == 2


# --- 4. compute_batches -----------------------------------------------------


def test_compute_batches_overlap_and_disjoint(state_dir: Path) -> None:
    """Overlapping intervals share a batch; a later disjoint one increments."""
    _write_json(
        state_dir / "subagent-tracking.json",
        {
            "agents": [
                {
                    "agent_id": "g1",
                    "agent_type": "executor",
                    "started_at": "2026-06-19T10:00:00Z",
                    "completed_at": "2026-06-19T10:10:00Z",
                    "status": "completed",
                    "duration_ms": 600000,
                },
                {
                    "agent_id": "g2",
                    "agent_type": "executor",
                    "started_at": "2026-06-19T10:05:00Z",
                    "completed_at": "2026-06-19T10:15:00Z",
                    "status": "completed",
                    "duration_ms": 600000,
                },
                {
                    "agent_id": "g3",
                    "agent_type": "executor",
                    "started_at": "2026-06-19T11:00:00Z",
                    "completed_at": "2026-06-19T11:05:00Z",
                    "status": "completed",
                    "duration_ms": 300000,
                },
            ]
        },
    )
    nodes = read_tracking(state_dir)
    batches = compute_batches(nodes)
    assert batches["g1"] == batches["g2"] == 0
    assert batches["g3"] == 1


def test_compute_batches_none_when_no_start() -> None:
    """An agent without ``started_at`` maps to a ``None`` batch."""
    from app.services.orchestration import AgentNodeData

    node = AgentNodeData(
        agent_id="x",
        agent_type="executor",
        status="unknown",
        started_at=None,
        completed_at=None,
        duration_ms=None,
    )
    assert compute_batches([node]) == {"x": None}


# --- 5. read_replay (dedup) -------------------------------------------------


def _replay_lines() -> list[dict]:
    """Return replay lines with t=0, an agent_start, and duplicated stops."""
    return [
        {"t": 0, "agent": "a735f6a", "agent_type": "planner",
         "event": "agent_start", "parent_mode": "none"},
        {"t": 0, "agent": "a735f6a", "agent_type": "planner",
         "event": "agent_stop", "success": True, "duration_ms": 100},
        {"t": 0, "agent": "a735f6a", "agent_type": "planner",
         "event": "agent_stop", "success": True, "duration_ms": 8244518},
        {"t": 0, "agent": "ae09f7e", "agent_type": "planner",
         "event": "agent_start", "parent_mode": "none"},
        {"t": 0, "agent": "a8e84d5", "agent_type": "git-master",
         "event": "agent_stop", "success": False, "duration_ms": 93455},
    ]


def test_read_replay_dedups_stops_keeping_max_duration(state_dir: Path) -> None:
    """Duplicate ``agent_stop`` events collapse to one with max duration."""
    _write_jsonl(state_dir / "agent-replay-x.jsonl", _replay_lines())
    events = read_replay(state_dir)
    by_agent = {e.agent: e for e in events if e.event == "agent_stop"}
    assert by_agent["a735f6a"].duration_ms == 8244518
    assert by_agent["a8e84d5"].success is False


def test_read_replay_counts_unique_starts(state_dir: Path) -> None:
    """Unique ``agent_start`` events count the distinct agents."""
    _write_jsonl(state_dir / "agent-replay-x.jsonl", _replay_lines())
    events = read_replay(state_dir)
    starts = {e.agent for e in events if e.event == "agent_start"}
    assert starts == {"a735f6a", "ae09f7e"}


def test_read_replay_missing_dir_is_empty(state_dir: Path) -> None:
    """No replay file yields an empty list."""
    assert read_replay(state_dir) == []


# --- 6. match_full_id -------------------------------------------------------


def test_match_full_id_prefix_match() -> None:
    """A short id matches the full id that it prefixes."""
    full = ["a735f6a53f86e040d", "ae09f7ed8f9924b3c"]
    assert match_full_id("a735f6a", full) == "a735f6a53f86e040d"


def test_match_full_id_no_match_returns_none() -> None:
    """A short id with no matching full id yields ``None``."""
    assert match_full_id("zzzzzzz", ["a735f6a53f86e040d"]) is None


# --- 7. build_timeline ------------------------------------------------------


def test_build_timeline_uses_tracking_time_and_replay_success(
    state_dir: Path,
) -> None:
    """Timeline ``at`` comes from tracking; ``success`` from matched replay."""
    _write_json(state_dir / "subagent-tracking.json", _tracking_payload())
    _write_jsonl(state_dir / "agent-replay-x.jsonl", _replay_lines())
    events = build_timeline(state_dir, "sess-1")
    assert all(isinstance(e, TimelineEvent) for e in events)
    stops = [e for e in events if e.event == "agent_stop"]
    completed = next(e for e in stops if e.agent_id == "a735f6a53f86e040d")
    expected = datetime(2026, 6, 19, 19, 24, 47, 452000, tzinfo=timezone.utc)
    assert completed.at == expected
    assert completed.success is True


def test_build_timeline_propagates_failure(state_dir: Path) -> None:
    """A ``success: false`` stop is propagated onto the matched event."""
    _write_json(
        state_dir / "subagent-tracking.json",
        {
            "agents": [
                {
                    "agent_id": "a8e84d5deadbeef00",
                    "agent_type": "oh-my-claudecode:git-master",
                    "started_at": "2026-06-19T10:00:00Z",
                    "completed_at": "2026-06-19T10:01:33Z",
                    "status": "failed",
                    "duration_ms": 93455,
                }
            ]
        },
    )
    _write_jsonl(state_dir / "agent-replay-x.jsonl", _replay_lines())
    events = build_timeline(state_dir, "sess-2")
    stop = next(e for e in events if e.event == "agent_stop")
    assert stop.success is False


# --- 8. build_agent_tree ----------------------------------------------------


def test_build_agent_tree_flat_sorted_no_hierarchy(state_dir: Path) -> None:
    """The tree is flat, time-ordered, and never claims a hierarchy."""
    _write_json(state_dir / "subagent-tracking.json", _tracking_payload())
    tree = build_agent_tree(state_dir, "sess-3")
    assert isinstance(tree, AgentTree)
    assert tree.session_id == "sess-3"
    assert tree.hierarchy_available is False
    started = [n.started_at for n in tree.nodes]
    assert started == sorted(started)
    assert tree.nodes[0].agent_id == "a735f6a53f86e040d"
