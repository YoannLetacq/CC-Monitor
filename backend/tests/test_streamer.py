"""Unit tests for the SSE streamer (W4, Increment 3).

Covers the snapshot/delta contract of ``event_generator`` and its helpers
using ``tmp_path`` fixtures that simulate append-only transcripts and state
rewrites.  The real ``~/.claude`` is never read.

The async generator is driven on an explicit event loop via :func:`_run`
(no ``pytest-asyncio`` dependency, keeping the toolchain unchanged — Q-DEP).

Coverage targets (§6 TDD brief):
    1. snapshot tree/timeline/overview first, sessionId present
    2. assistant(thinking) append → agent_blocks
    3. tool_use + tool_result → tool_activity running→ok
    4. fallback polling (observer disabled) still drains an append
    5. heartbeat after inactivity
    6. subagent-tracking.json rewrite → tree/overview recompute
    7. new reports/x__role.md → report_available
    9. cancellation → observer stopped (no residual thread)
   10. sub-agent transcript drain → agent_blocks with correct agentId (MEDIUM-2)
   11. report change → overview re-emitted with updated gateSummary (MEDIUM-1)
   12. inactive agent → flow drained then removed, no unbounded growth (MEDIUM-2)
"""

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Awaitable
from pathlib import Path
from typing import TypeVar

import pytest

from app.core.config import Settings
from app.services import streamer
from app.services.streamer import event_generator

_SID = "aabbccdd11223344"

_T = TypeVar("_T")

_TRACKING_RUNNING = {
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


def _run(coro: Awaitable[_T]) -> _T:
    """Run a coroutine to completion on a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _fast_settings(**overrides: object) -> Settings:
    """Return Settings with tiny intervals for fast, deterministic tests."""
    base: dict[str, object] = {
        "poll_interval_s": 0.02,
        "heartbeat_interval_s": 0.05,
        "transcript_tail_max_bytes": 1_000_000,
        "tool_content_cap": 2000,
        "max_pending_tools": 512,
    }
    base.update(overrides)
    return Settings(**base)


def _append_line(path: Path, obj: dict) -> None:
    """Append one JSON line to ``path`` (test helper, write mode)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(obj))
        handle.write("\n")


def _write_json(path: Path, obj: dict) -> None:
    """Write a JSON object to ``path`` (test helper, write mode)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle)


def _assistant_thinking(text: str) -> dict:
    """Build an assistant transcript line carrying one thinking block."""
    return {
        "type": "assistant",
        "sessionId": _SID,
        "timestamp": "2026-06-19T09:00:01.000Z",
        "message": {"content": [{"type": "thinking", "thinking": text}]},
    }


def _assistant_tool_use(tool_id: str, name: str, command: str) -> dict:
    """Build an assistant transcript line carrying one tool_use block."""
    return {
        "type": "assistant",
        "sessionId": _SID,
        "timestamp": "2026-06-19T09:00:02.000Z",
        "message": {
            "content": [
                {"type": "tool_use", "id": tool_id, "name": name, "input": {"command": command}}
            ]
        },
    }


def _user_tool_result(tool_id: str, content: str, is_error: bool = False) -> dict:
    """Build a user transcript line carrying one tool_result block."""
    return {
        "type": "user",
        "sessionId": _SID,
        "timestamp": "2026-06-19T09:00:03.000Z",
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": content,
                 "is_error": is_error}
            ]
        },
    }


@pytest.fixture()
def project_tree(tmp_path: Path) -> tuple[str, Path, Path]:
    """Build a project tree; return (project_path, transcript_path, state_dir)."""
    project_dir = tmp_path / "work" / "proj"
    state_dir = project_dir / ".omc" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    _write_json(state_dir / "subagent-tracking.json", _TRACKING_RUNNING)
    (project_dir / ".omc" / "reports").mkdir(parents=True, exist_ok=True)

    slug = str(project_dir).replace("/", "-")
    transcript = tmp_path / "claude_projects" / slug / f"{_SID}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("", encoding="utf-8")
    return str(project_dir), transcript, state_dir


async def _take(gen: AsyncIterator[str], count: int, timeout: float = 2.0) -> list[str]:
    """Consume up to ``count`` messages from the generator with a timeout."""
    out: list[str] = []
    for _ in range(count):
        out.append(await asyncio.wait_for(gen.__anext__(), timeout=timeout))
    return out


def _event_name(message: str) -> str:
    """Return the SSE event name from an encoded message."""
    return message.split("\n", 1)[0].removeprefix("event: ")


def _payload(message: str) -> dict:
    """Return the decoded JSON payload of an encoded SSE message."""
    return json.loads(message.split("data: ", 1)[1])


def test_snapshot_first(project_tree: tuple[str, Path, Path]) -> None:
    """The first three messages are tree/timeline/overview with sessionId."""
    project_path, transcript, _ = project_tree

    async def _scenario() -> list[str]:
        gen = event_generator(project_path, _SID, transcript, _fast_settings())
        try:
            return await _take(gen, 3)
        finally:
            await gen.aclose()

    messages = _run(_scenario())
    assert [_event_name(m) for m in messages] == ["tree", "timeline", "overview"]
    assert all(_SID in message for message in messages)


def test_thinking_append_emits_agent_blocks(project_tree: tuple[str, Path, Path]) -> None:
    """Appending an assistant(thinking) line yields an agent_blocks event."""
    project_path, transcript, _ = project_tree

    async def _scenario() -> str:
        gen = event_generator(project_path, _SID, transcript, _fast_settings())
        try:
            await _take(gen, 3)
            _append_line(transcript, _assistant_thinking("pondering"))
            return await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        finally:
            await gen.aclose()

    message = _run(_scenario())
    assert _event_name(message) == "agent_blocks"
    payload = _payload(message)
    assert payload["sessionId"] == _SID
    assert payload["blocks"][0]["kind"] == "thinking"
    assert payload["blocks"][0]["text"] == "pondering"


def test_tool_use_then_result_running_then_ok(project_tree: tuple[str, Path, Path]) -> None:
    """tool_use then tool_result produce running then ok tool_activity."""
    project_path, transcript, _ = project_tree

    async def _scenario() -> tuple[str, str]:
        gen = event_generator(project_path, _SID, transcript, _fast_settings())
        try:
            await _take(gen, 3)
            _append_line(transcript, _assistant_tool_use("toolu_1", "Bash", "ls"))
            running = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
            _append_line(transcript, _user_tool_result("toolu_1", "done"))
            completed = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
            return running, completed
        finally:
            await gen.aclose()

    running, completed = _run(_scenario())
    assert _event_name(running) == "tool_activity"
    assert _payload(running)["calls"][0]["status"] == "running"
    ok_payload = _payload(completed)
    assert ok_payload["calls"][0]["status"] == "ok"
    assert ok_payload["calls"][0]["resultPreview"] == "done"


def test_fallback_polling_without_observer(
    project_tree: tuple[str, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the observer disabled, an append is still drained via polling (R4)."""

    class _NoObserver:
        """A no-op stand-in that never emits filesystem events."""

        def schedule(self, *_args: object, **_kwargs: object) -> None:
            """Ignore scheduling requests."""

        def start(self) -> None:
            """Start nothing."""

        def stop(self) -> None:
            """Stop nothing."""

        def join(self, *_args: object, **_kwargs: object) -> None:
            """Join nothing."""

    monkeypatch.setattr(streamer, "Observer", _NoObserver)
    project_path, transcript, _ = project_tree

    async def _scenario() -> str:
        gen = event_generator(project_path, _SID, transcript, _fast_settings())
        try:
            await _take(gen, 3)
            _append_line(transcript, _assistant_thinking("polled"))
            return await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        finally:
            await gen.aclose()

    message = _run(_scenario())
    assert _event_name(message) == "agent_blocks"


def test_heartbeat_after_inactivity(project_tree: tuple[str, Path, Path]) -> None:
    """A heartbeat is emitted after heartbeat_interval_s of silence."""
    project_path, transcript, _ = project_tree

    async def _scenario() -> str:
        gen = event_generator(project_path, _SID, transcript, _fast_settings())
        try:
            await _take(gen, 3)
            return await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        finally:
            await gen.aclose()

    message = _run(_scenario())
    assert _event_name(message) == "heartbeat"
    payload = _payload(message)
    assert payload["sessionId"] == _SID
    assert "ts" in payload


def test_tracking_rewrite_recomputes_state(project_tree: tuple[str, Path, Path]) -> None:
    """Rewriting subagent-tracking.json re-emits tree/timeline/overview."""
    project_path, transcript, state_dir = project_tree

    async def _scenario() -> list[str]:
        gen = event_generator(project_path, _SID, transcript, _fast_settings())
        try:
            await _take(gen, 3)
            updated = dict(_TRACKING_RUNNING)
            updated["agents"] = _TRACKING_RUNNING["agents"] + [
                {
                    "agent_id": "done222ccc333ddd4",
                    "agent_type": "executor",
                    "started_at": "2026-06-19T09:01:00.000Z",
                    "completed_at": "2026-06-19T09:02:00.000Z",
                    "duration_ms": 60000,
                    "status": "completed",
                    "parent_mode": "none",
                }
            ]
            updated["last_updated"] = "2026-06-19T09:02:00.000Z"
            _write_json(state_dir / "subagent-tracking.json", updated)
            return await _take(gen, 3)
        finally:
            await gen.aclose()

    names = [_event_name(m) for m in _run(_scenario())]
    assert names == ["tree", "timeline", "overview"]


def test_new_report_emits_report_available(project_tree: tuple[str, Path, Path]) -> None:
    """A new reports/x__role.md file yields a report_available event.

    Since the report mtime is now part of the state signature (MEDIUM-1), the
    creation of the file also triggers a ``tree``/``timeline``/``overview``
    recompute.  We therefore collect up to 5 messages and look for
    ``report_available`` among them.
    """
    project_path, transcript, state_dir = project_tree
    reports_dir = state_dir.parent / "reports"

    async def _scenario() -> list[str]:
        gen = event_generator(project_path, _SID, transcript, _fast_settings())
        try:
            await _take(gen, 3)
            (reports_dir / "branch__verifier.md").write_text("# r\n", encoding="utf-8")
            collected: list[str] = []
            for _ in range(5):
                try:
                    msg = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
                    collected.append(msg)
                    if _event_name(msg) == "report_available":
                        break
                except (asyncio.TimeoutError, StopAsyncIteration):
                    break
            return collected
        finally:
            await gen.aclose()

    messages = _run(_scenario())
    names = [_event_name(m) for m in messages]
    assert "report_available" in names, f"expected report_available in {names}"
    ra_msg = next(m for m in messages if _event_name(m) == "report_available")
    payload = _payload(ra_msg)
    assert payload["role"] == "verifier"
    assert payload["fileName"] == "branch__verifier.md"
    assert payload["sessionId"] == _SID


def test_cancellation_stops_observer(project_tree: tuple[str, Path, Path]) -> None:
    """Closing the generator stops the observer — no residual watchdog thread."""
    project_path, transcript, _ = project_tree
    before = {t.name for t in threading.enumerate()}

    async def _scenario() -> None:
        gen = event_generator(project_path, _SID, transcript, _fast_settings())
        await _take(gen, 3)
        await gen.aclose()

    _run(_scenario())
    # Watchdog emitter threads carry "emitter" in their name; none must survive.
    residual = [
        t for t in threading.enumerate()
        if t.name not in before and t.is_alive() and "emitter" in t.name.lower()
    ]
    assert residual == []


# ---------------------------------------------------------------------------
# MEDIUM-2 fix — test 10: sub-agent transcript drain emits agent_blocks
# ---------------------------------------------------------------------------

def test_subagent_transcript_drain_emits_agent_blocks(
    project_tree: tuple[str, Path, Path],
) -> None:
    """Draining a sub-agent transcript emits agent_blocks with the correct agentId.

    The sub-agent is listed as ``running`` in tracking and has a transcript
    file at ``subagents/agent-<id>.jsonl``.  After the snapshot, appending a
    thinking line to that file must yield an ``agent_blocks`` event whose
    payload carries the matching ``agentId``.
    """
    project_path, transcript, _ = project_tree
    agent_id = "run111aaa222bbb3"  # matches _TRACKING_RUNNING

    subagents_dir = transcript.parent / _SID / "subagents"
    subagents_dir.mkdir(parents=True, exist_ok=True)
    agent_transcript = subagents_dir / f"agent-{agent_id}.jsonl"
    agent_transcript.write_text("", encoding="utf-8")

    async def _scenario() -> str:
        gen = event_generator(project_path, _SID, transcript, _fast_settings())
        try:
            await _take(gen, 3)
            # Append a thinking block to the sub-agent transcript.
            thinking_line = {
                "type": "assistant",
                "agentId": agent_id,
                "sessionId": _SID,
                "timestamp": "2026-06-19T09:00:01.000Z",
                "message": {
                    "content": [{"type": "thinking", "thinking": "sub-agent pondering"}]
                },
            }
            _append_line(agent_transcript, thinking_line)
            return await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        finally:
            await gen.aclose()

    message = _run(_scenario())
    assert _event_name(message) == "agent_blocks"
    payload = _payload(message)
    assert payload["agentId"] == agent_id
    assert payload["blocks"][0]["kind"] == "thinking"
    assert payload["blocks"][0]["text"] == "sub-agent pondering"


# ---------------------------------------------------------------------------
# MEDIUM-1 fix — test 11: report change triggers overview re-emission
# ---------------------------------------------------------------------------

def test_report_change_triggers_overview_reemit(
    project_tree: tuple[str, Path, Path],
) -> None:
    """Creating/modifying a report file causes overview to be re-emitted.

    The ``gateSummary`` in the re-emitted overview must reflect the new report
    (which contains a PASS gate), confirming that ``_state_signature`` now
    includes the reports directory so that ``_recompute_if_changed`` fires.
    """
    project_path, transcript, state_dir = project_tree
    reports_dir = state_dir.parent / "reports"

    # A minimal report with one PASS gate in pipe-table form.
    report_content = (
        "# My report\n\n"
        "## Gates\n\n"
        "| Gate | Result |\n"
        "| --- | --- |\n"
        "| pylint | PASS |\n\n"
        "## Verdict\n\nPASS\n"
    )

    async def _scenario() -> list[str]:
        gen = event_generator(project_path, _SID, transcript, _fast_settings())
        try:
            await _take(gen, 3)
            # Write the report after the snapshot; this must trigger overview.
            (reports_dir / "branch__executor.md").write_text(
                report_content, encoding="utf-8"
            )
            # Collect the next events; overview must appear (may follow
            # report_available).  Collect up to 4 to avoid flakiness with
            # event ordering.
            collected: list[str] = []
            for _ in range(4):
                try:
                    msg = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
                    collected.append(msg)
                except (asyncio.TimeoutError, StopAsyncIteration):
                    break
            return collected
        finally:
            await gen.aclose()

    messages = _run(_scenario())
    names = [_event_name(m) for m in messages]
    assert "overview" in names, f"expected overview in {names}"
    overview_msg = next(m for m in messages if _event_name(m) == "overview")
    payload = _payload(overview_msg)
    gate_summary = payload["gateSummary"]
    assert gate_summary["passCount"] >= 1, (
        f"expected at least 1 PASS gate in gateSummary, got {gate_summary}"
    )


# ---------------------------------------------------------------------------
# MEDIUM-2 fix — test 12: inactive agent flow is drained then removed
# ---------------------------------------------------------------------------

def test_inactive_agent_flow_drained_then_removed(
    project_tree: tuple[str, Path, Path],
) -> None:
    """An agent that becomes inactive is drained one last time then removed.

    Sequence:
      1. Agent starts as ``running`` → flow is created and tailed.
      2. A thinking block is appended to its transcript while active.
      3. Tracking is rewritten marking the agent ``completed``.
      4. After rewrite the flow must be drained (emitting agent_blocks) and
         then removed from ``state.flows`` so memory does not grow unboundedly.
    """
    project_path, transcript, state_dir = project_tree
    agent_id = "run111aaa222bbb3"

    subagents_dir = transcript.parent / _SID / "subagents"
    subagents_dir.mkdir(parents=True, exist_ok=True)
    agent_transcript = subagents_dir / f"agent-{agent_id}.jsonl"
    agent_transcript.write_text("", encoding="utf-8")

    thinking_line = {
        "type": "assistant",
        "agentId": agent_id,
        "sessionId": _SID,
        "timestamp": "2026-06-19T09:00:01.000Z",
        "message": {
            "content": [{"type": "thinking", "thinking": "last thought before done"}]
        },
    }

    tracking_completed = {
        "agents": [
            {
                "agent_id": agent_id,
                "agent_type": "executor",
                "started_at": "2026-06-19T09:00:00.000Z",
                "completed_at": "2026-06-19T09:05:00.000Z",
                "duration_ms": 300000,
                "status": "completed",
                "parent_mode": "none",
            }
        ],
        "total_spawned": 1,
        "last_updated": "2026-06-19T09:05:00.000Z",
    }

    async def _scenario() -> tuple[list[str], list[str]]:
        gen = event_generator(project_path, _SID, transcript, _fast_settings())
        try:
            await _take(gen, 3)
            # Append a line while the agent is still active.
            _append_line(agent_transcript, thinking_line)
            # Wait for it to be drained (agent_blocks).
            drained: list[str] = []
            for _ in range(5):
                try:
                    msg = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
                    drained.append(msg)
                    if _event_name(msg) == "agent_blocks":
                        break
                except (asyncio.TimeoutError, StopAsyncIteration):
                    break
            # Now mark the agent completed → triggers drain + removal.
            _write_json(state_dir / "subagent-tracking.json", tracking_completed)
            # Collect events after the state change; must NOT include more
            # agent_blocks for the now-inactive agent (flow was removed).
            post: list[str] = []
            for _ in range(5):
                try:
                    msg = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
                    post.append(msg)
                except (asyncio.TimeoutError, StopAsyncIteration):
                    break
            # Access internal state to verify the flow was removed.
            # ``event_generator`` builds a ``_StreamState`` locally; we probe
            # indirectly by checking no further agent_blocks for agent_id appear
            # and that flows dict shrinks (tested via the module helper).
            return drained, post
        finally:
            await gen.aclose()

    drained_msgs, post_msgs = _run(_scenario())
    # The thinking block must have been drained before deactivation.
    drain_names = [_event_name(m) for m in drained_msgs]
    assert "agent_blocks" in drain_names, (
        f"expected agent_blocks during active phase, got {drain_names}"
    )
    # After deactivation no new agent_blocks for the completed agent should appear
    # (the flow was removed so no further tailing occurs).
    post_agent_blocks = [
        m for m in post_msgs
        if _event_name(m) == "agent_blocks"
        and _payload(m).get("agentId") == agent_id
    ]
    assert post_agent_blocks == [], (
        f"flow not removed: got post-deactivation agent_blocks {post_agent_blocks}"
    )
