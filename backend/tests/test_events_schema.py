"""Tests for SSE event schemas and SSE encoder (W3 — TDD red-first, §5 INCR3_DISPATCH).

Covers the four required cases from the brief:
  1. ReasoningBlock camelCase aliases + redacted_thinking text=None.
  2. ToolCall camelCase aliases (toolUseId, inputSummary, resultPreview).
  3. encode_sse format: starts with event:/data:, ends with \\n\\n, JSON contains sessionId.
  4. Round-trip construction by snake_case name (populate_by_name=True).
"""

import json
from datetime import datetime, timezone

import pytest

from app.schemas.events import (
    AgentBlocksEvent,
    BlockKind,
    HeartbeatEvent,
    ReasoningBlock,
    ReportAvailableEvent,
    TimelineEventBatch,
    ToolActivityEvent,
    ToolCall,
    ToolStatus,
    encode_sse,
)


# ---------------------------------------------------------------------------
# Test 1 — ReasoningBlock: camelCase aliases + redacted_thinking text=None
# ---------------------------------------------------------------------------

class TestReasoningBlock:
    """Verify ReasoningBlock alias generation and field defaults."""

    def test_redacted_thinking_camel_case_aliases(self) -> None:
        """model_dump with by_alias=True must produce agentId and text=None."""
        block = ReasoningBlock(
            kind="redacted_thinking",
            text=None,
            agent_id="agent-abc",
            ts=None,
        )
        dumped = block.model_dump(by_alias=True)
        assert dumped["kind"] == "redacted_thinking"
        assert dumped["text"] is None
        assert "agentId" in dumped
        assert dumped["agentId"] == "agent-abc"
        assert "agent_id" not in dumped

    def test_thinking_block(self) -> None:
        """thinking kind with text must serialize correctly."""
        block = ReasoningBlock(kind="thinking", text="I think therefore I am")
        dumped = block.model_dump(by_alias=True)
        assert dumped["kind"] == "thinking"
        assert dumped["text"] == "I think therefore I am"
        assert dumped["agentId"] is None
        assert dumped["ts"] is None

    def test_text_block(self) -> None:
        """text kind must also serialize with camelCase keys."""
        block = ReasoningBlock(kind="text", text="some output", agent_id=None, ts=None)
        dumped = block.model_dump(by_alias=True)
        assert dumped["kind"] == "text"
        assert "agentId" in dumped


# ---------------------------------------------------------------------------
# Test 2 — ToolCall: camelCase aliases
# ---------------------------------------------------------------------------

class TestToolCall:
    """Verify ToolCall alias generation for multi-word snake_case fields."""

    def test_camel_case_field_names(self) -> None:
        """toolUseId, inputSummary, resultPreview must appear in camelCase."""
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        call = ToolCall(
            tool_use_id="toolu_abc123",
            name="Bash",
            input_summary="echo hello",
            status="ok",
            result_preview="hello",
            agent_id="agent-xyz",
            ts=ts,
        )
        dumped = call.model_dump(by_alias=True)
        assert "toolUseId" in dumped
        assert dumped["toolUseId"] == "toolu_abc123"
        assert "inputSummary" in dumped
        assert dumped["inputSummary"] == "echo hello"
        assert "resultPreview" in dumped
        assert dumped["resultPreview"] == "hello"
        assert "agentId" in dumped
        assert dumped["agentId"] == "agent-xyz"
        # snake_case keys must NOT appear
        assert "tool_use_id" not in dumped
        assert "input_summary" not in dumped
        assert "result_preview" not in dumped

    def test_running_status_nullable_fields(self) -> None:
        """A running ToolCall with no result should serialize without errors."""
        call = ToolCall(
            tool_use_id="toolu_running",
            name="Read",
            input_summary=None,
            status="running",
            result_preview=None,
            agent_id=None,
            ts=None,
        )
        dumped = call.model_dump(by_alias=True)
        assert dumped["status"] == "running"
        assert dumped["resultPreview"] is None

    def test_error_status(self) -> None:
        """error status must round-trip correctly."""
        call = ToolCall(
            tool_use_id="toolu_err",
            name="Write",
            status="error",
        )
        assert call.status == "error"


# ---------------------------------------------------------------------------
# Test 3 — encode_sse: format contract
# ---------------------------------------------------------------------------

class TestEncodeSSE:
    """Verify the SSE wire format produced by encode_sse."""

    def test_heartbeat_format(self) -> None:
        """encode_sse must start with 'event: heartbeat\\ndata: {', end with '\\n\\n'."""
        ts = datetime(2024, 6, 21, 12, 0, 0, tzinfo=timezone.utc)
        event_model = HeartbeatEvent(session_id="sess-001", ts=ts)
        result = encode_sse("heartbeat", event_model)
        assert result.startswith("event: heartbeat\ndata: {")
        assert result.endswith("\n\n")

    def test_heartbeat_json_contains_session_id(self) -> None:
        """The JSON payload must contain 'sessionId' (camelCase)."""
        ts = datetime(2024, 6, 21, 12, 0, 0, tzinfo=timezone.utc)
        event_model = HeartbeatEvent(session_id="sess-001", ts=ts)
        result = encode_sse("heartbeat", event_model)
        # Extract the JSON portion after "data: "
        data_line = result.split("\n")[1]
        assert data_line.startswith("data: ")
        payload = json.loads(data_line[len("data: "):])
        assert "sessionId" in payload
        assert payload["sessionId"] == "sess-001"
        assert "session_id" not in payload

    def test_sse_double_newline_termination(self) -> None:
        """Every SSE message must end with exactly two newline characters."""
        ts = datetime(2024, 6, 21, 8, 0, 0, tzinfo=timezone.utc)
        event_model = HeartbeatEvent(session_id="x", ts=ts)
        result = encode_sse("heartbeat", event_model)
        assert result[-2:] == "\n\n"

    def test_agent_blocks_event_sse(self) -> None:
        """AgentBlocksEvent must encode correctly via encode_sse."""
        blocks = [
            ReasoningBlock(kind="thinking", text="reasoning", agent_id=None, ts=None)
        ]
        event_model = AgentBlocksEvent(
            session_id="sess-002", agent_id=None, blocks=blocks
        )
        result = encode_sse("agent_blocks", event_model)
        assert result.startswith("event: agent_blocks\ndata: {")
        assert result.endswith("\n\n")
        data_line = result.split("\n")[1]
        payload = json.loads(data_line[len("data: "):])
        assert payload["sessionId"] == "sess-002"


# ---------------------------------------------------------------------------
# Test 4 — Round-trip construction by snake_case (populate_by_name=True)
# ---------------------------------------------------------------------------

class TestPopulateByName:
    """Verify that models can be constructed using snake_case field names."""

    def test_reasoning_block_snake_case_construction(self) -> None:
        """ReasoningBlock must accept snake_case kwargs (populate_by_name=True)."""
        block = ReasoningBlock(
            kind="text",
            text="hello",
            agent_id="agent-1",
            ts=None,
        )
        assert block.agent_id == "agent-1"
        assert block.kind == "text"

    def test_tool_call_snake_case_construction(self) -> None:
        """ToolCall must accept snake_case kwargs."""
        call = ToolCall(
            tool_use_id="toolu_x",
            name="Edit",
            input_summary="path/to/file",
            status="running",
            result_preview=None,
            agent_id=None,
            ts=None,
        )
        assert call.tool_use_id == "toolu_x"
        assert call.input_summary == "path/to/file"
        assert call.result_preview is None

    def test_heartbeat_snake_case_construction(self) -> None:
        """HeartbeatEvent must accept session_id (snake_case) directly."""
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        hb = HeartbeatEvent(session_id="s1", ts=ts)
        assert hb.session_id == "s1"
        assert hb.ts == ts

    def test_report_available_event(self) -> None:
        """ReportAvailableEvent must serialize role and fileName correctly."""
        ev = ReportAvailableEvent(
            session_id="sess-3",
            role="executor",
            file_name="backend-sse__events-schema.md",
        )
        dumped = ev.model_dump(by_alias=True)
        assert dumped["sessionId"] == "sess-3"
        assert dumped["role"] == "executor"
        assert dumped["fileName"] == "backend-sse__events-schema.md"

    def test_tool_activity_event(self) -> None:
        """ToolActivityEvent must serialize calls list with camelCase."""
        calls = [
            ToolCall(
                tool_use_id="toolu_1",
                name="Bash",
                status="ok",
            )
        ]
        ev = ToolActivityEvent(session_id="sess-4", agent_id="agent-A", calls=calls)
        dumped = ev.model_dump(by_alias=True)
        assert dumped["sessionId"] == "sess-4"
        assert dumped["agentId"] == "agent-A"
        assert len(dumped["calls"]) == 1
        assert dumped["calls"][0]["toolUseId"] == "toolu_1"


# ---------------------------------------------------------------------------
# Test — Literal types are correctly constrained
# ---------------------------------------------------------------------------

class TestLiteralTypes:
    """Verify ToolStatus and BlockKind Literal constraints."""

    @pytest.mark.parametrize("status", ["running", "ok", "error"])
    def test_tool_status_valid_values(self, status: ToolStatus) -> None:
        """All valid ToolStatus values must be accepted."""
        call = ToolCall(tool_use_id="id", name="n", status=status)
        assert call.status == status

    @pytest.mark.parametrize("kind", ["thinking", "text", "redacted_thinking"])
    def test_block_kind_valid_values(self, kind: BlockKind) -> None:
        """All valid BlockKind values must be accepted."""
        block = ReasoningBlock(kind=kind)
        assert block.kind == kind
