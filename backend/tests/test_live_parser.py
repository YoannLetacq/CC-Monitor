"""Tests for app.services.live_parser (W2, Increment 3).

Covers all 10 cases from INCR3_DISPATCH §4:
  1. thinking + text -> 2 ReasoningBlocks with correct kind
  2. redacted_thinking -> text=None
  3. Unknown block type (server_tool_use) -> ignored
  4. tool_use alone -> 1 ToolCall with status="running"
  5. tool_use then tool_result(is_error=False) -> running then ok
  6. tool_result(is_error=True) -> status="error"
  7. content as list [{type:text,text}] -> normalised to str
  8. inputSummary / resultPreview truncated at cap
  9. orphan tool_result -> defensive ToolCall
  10. eviction beyond max_pending
"""

import pytest

from app.services.live_parser import (
    ToolPairer,
    _normalize_content,
    _summarize,
    extract_reasoning,
)


# ---------------------------------------------------------------------------
# Helpers for building fake transcript lines
# ---------------------------------------------------------------------------

def _assistant_line(*blocks: dict, agent_id: str | None = None) -> dict:
    """Build a fake assistant transcript line with the given content blocks."""
    line: dict = {
        "type": "assistant",
        "timestamp": "2024-01-01T00:00:00Z",
        "message": {"content": list(blocks)},
    }
    if agent_id is not None:
        line["agentId"] = agent_id
    return line


def _user_line(*blocks: dict) -> dict:
    """Build a fake user transcript line with the given content blocks."""
    return {
        "type": "user",
        "timestamp": "2024-01-01T00:00:01Z",
        "message": {"content": list(blocks)},
    }


def _thinking_block(text: str) -> dict:
    return {"type": "thinking", "thinking": text}


def _text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def _redacted_block() -> dict:
    return {"type": "redacted_thinking", "data": "opaque"}


def _tool_use_block(tool_id: str, name: str, tool_input: dict) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


def _tool_result_block(
    tool_use_id: str,
    content: str | list,
    is_error: bool = False,
) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
        "is_error": is_error,
    }


def _server_tool_use_block(tool_id: str, name: str) -> dict:
    return {"type": "server_tool_use", "id": tool_id, "name": name}


# ---------------------------------------------------------------------------
# Case 1: thinking + text -> 2 ReasoningBlocks with correct kind
# ---------------------------------------------------------------------------

class TestExtractReasoningThinkingAndText:
    """Case 1: thinking and text blocks are extracted with correct kinds."""

    def test_two_blocks_returned(self) -> None:
        line = _assistant_line(_thinking_block("I think..."), _text_block("Hello"))
        blocks = extract_reasoning(line)
        assert len(blocks) == 2

    def test_thinking_block_kind(self) -> None:
        line = _assistant_line(_thinking_block("I think..."), _text_block("Hello"))
        blocks = extract_reasoning(line)
        assert blocks[0].kind == "thinking"
        assert blocks[0].text == "I think..."

    def test_text_block_kind(self) -> None:
        line = _assistant_line(_thinking_block("I think..."), _text_block("Hello"))
        blocks = extract_reasoning(line)
        assert blocks[1].kind == "text"
        assert blocks[1].text == "Hello"

    def test_agent_id_propagated(self) -> None:
        line = _assistant_line(_thinking_block("x"), agent_id="agent-abc")
        blocks = extract_reasoning(line)
        assert blocks[0].agent_id == "agent-abc"

    def test_no_agent_id_is_none(self) -> None:
        line = _assistant_line(_thinking_block("x"))
        blocks = extract_reasoning(line)
        assert blocks[0].agent_id is None

    def test_timestamp_parsed(self) -> None:
        line = _assistant_line(_thinking_block("x"))
        blocks = extract_reasoning(line)
        assert blocks[0].ts is not None

    def test_non_assistant_line_returns_empty(self) -> None:
        line = _user_line(_text_block("Hi"))
        assert extract_reasoning(line) == []

    def test_empty_content_returns_empty(self) -> None:
        line = _assistant_line()
        assert extract_reasoning(line) == []


# ---------------------------------------------------------------------------
# Case 2: redacted_thinking -> text=None
# ---------------------------------------------------------------------------

class TestExtractReasoningRedactedThinking:
    """Case 2: redacted_thinking block has kind="redacted_thinking" and text=None."""

    def test_kind_is_redacted_thinking(self) -> None:
        line = _assistant_line(_redacted_block())
        blocks = extract_reasoning(line)
        assert len(blocks) == 1
        assert blocks[0].kind == "redacted_thinking"

    def test_text_is_none(self) -> None:
        line = _assistant_line(_redacted_block())
        blocks = extract_reasoning(line)
        assert blocks[0].text is None


# ---------------------------------------------------------------------------
# Case 3: unknown block type -> ignored
# ---------------------------------------------------------------------------

class TestExtractReasoningIgnoresUnknownBlocks:
    """Case 3: blocks with unknown types (server_tool_use, tool_use, etc.) are ignored."""

    def test_server_tool_use_ignored(self) -> None:
        line = _assistant_line(_server_tool_use_block("id1", "bash"))
        assert extract_reasoning(line) == []

    def test_tool_use_ignored_in_reasoning(self) -> None:
        line = _assistant_line(_tool_use_block("id1", "Bash", {"command": "ls"}))
        assert extract_reasoning(line) == []

    def test_mixed_known_and_unknown(self) -> None:
        line = _assistant_line(
            _thinking_block("think"),
            _server_tool_use_block("id1", "bash"),
            _text_block("done"),
        )
        blocks = extract_reasoning(line)
        assert len(blocks) == 2
        assert blocks[0].kind == "thinking"
        assert blocks[1].kind == "text"


# ---------------------------------------------------------------------------
# Case 4: tool_use alone -> 1 ToolCall with status="running"
# ---------------------------------------------------------------------------

class TestToolPaierFeedToolUseAlone:
    """Case 4: a tool_use block alone emits one running ToolCall."""

    def test_returns_one_call(self) -> None:
        pairer = ToolPairer()
        line = _assistant_line(_tool_use_block("id1", "Bash", {"command": "ls"}))
        calls = pairer.feed(line)
        assert len(calls) == 1

    def test_status_is_running(self) -> None:
        pairer = ToolPairer()
        line = _assistant_line(_tool_use_block("id1", "Bash", {"command": "ls"}))
        calls = pairer.feed(line)
        assert calls[0].status == "running"

    def test_tool_use_id_preserved(self) -> None:
        pairer = ToolPairer()
        line = _assistant_line(_tool_use_block("id1", "Bash", {"command": "ls"}))
        calls = pairer.feed(line)
        assert calls[0].tool_use_id == "id1"

    def test_name_preserved(self) -> None:
        pairer = ToolPairer()
        line = _assistant_line(_tool_use_block("id1", "Bash", {"command": "ls"}))
        calls = pairer.feed(line)
        assert calls[0].name == "Bash"

    def test_result_preview_is_none_while_running(self) -> None:
        pairer = ToolPairer()
        line = _assistant_line(_tool_use_block("id1", "Bash", {"command": "ls"}))
        calls = pairer.feed(line)
        assert calls[0].result_preview is None

    def test_multiple_tool_uses_in_one_line(self) -> None:
        pairer = ToolPairer()
        line = _assistant_line(
            _tool_use_block("id1", "Bash", {"command": "ls"}),
            _tool_use_block("id2", "Read", {"file_path": "/f"}),
        )
        calls = pairer.feed(line)
        assert len(calls) == 2
        assert all(c.status == "running" for c in calls)


# ---------------------------------------------------------------------------
# Case 5: tool_use then tool_result(is_error=False) -> ok
# ---------------------------------------------------------------------------

class TestToolPairerToolUseAndToolResult:
    """Case 5: paired tool_use + tool_result transitions to ok."""

    def test_tool_result_emits_ok(self) -> None:
        pairer = ToolPairer()
        pairer.feed(_assistant_line(_tool_use_block("id1", "Read", {"file_path": "/x"})))
        calls = pairer.feed(_user_line(_tool_result_block("id1", "file contents")))
        assert len(calls) == 1
        assert calls[0].status == "ok"

    def test_tool_use_id_matches(self) -> None:
        pairer = ToolPairer()
        pairer.feed(_assistant_line(_tool_use_block("id1", "Read", {"file_path": "/x"})))
        calls = pairer.feed(_user_line(_tool_result_block("id1", "file contents")))
        assert calls[0].tool_use_id == "id1"

    def test_result_preview_populated(self) -> None:
        pairer = ToolPairer()
        pairer.feed(_assistant_line(_tool_use_block("id1", "Read", {"file_path": "/x"})))
        calls = pairer.feed(_user_line(_tool_result_block("id1", "content here")))
        assert calls[0].result_preview == "content here"

    def test_name_from_tool_use_preserved(self) -> None:
        pairer = ToolPairer()
        pairer.feed(_assistant_line(_tool_use_block("id1", "Read", {"file_path": "/x"})))
        calls = pairer.feed(_user_line(_tool_result_block("id1", "data")))
        assert calls[0].name == "Read"

    def test_pending_cleared_after_result(self) -> None:
        """After tool_result, the id is removed from pending."""
        pairer = ToolPairer()
        pairer.feed(_assistant_line(_tool_use_block("id1", "Read", {"file_path": "/x"})))
        pairer.feed(_user_line(_tool_result_block("id1", "data")))
        # Second tool_result with same id -> orphan (id no longer pending)
        calls = pairer.feed(_user_line(_tool_result_block("id1", "again")))
        assert calls[0].name == ""  # defensive orphan


# ---------------------------------------------------------------------------
# Case 6: tool_result(is_error=True) -> status="error"
# ---------------------------------------------------------------------------

class TestToolPairerIsError:
    """Case 6: is_error=True sets status to "error"."""

    def test_status_is_error(self) -> None:
        pairer = ToolPairer()
        pairer.feed(_assistant_line(_tool_use_block("id1", "Bash", {"command": "bad"})))
        calls = pairer.feed(_user_line(_tool_result_block("id1", "err msg", is_error=True)))
        assert calls[0].status == "error"

    def test_result_preview_populated_on_error(self) -> None:
        pairer = ToolPairer()
        pairer.feed(_assistant_line(_tool_use_block("id1", "Bash", {"command": "bad"})))
        calls = pairer.feed(_user_line(_tool_result_block("id1", "err msg", is_error=True)))
        assert calls[0].result_preview == "err msg"


# ---------------------------------------------------------------------------
# Case 7: content as list [{type:text,text}] -> normalised to str
# ---------------------------------------------------------------------------

class TestNormalizeContent:
    """Case 7: list content is joined; str content is returned as-is."""

    def test_str_content_returned_as_is(self) -> None:
        assert _normalize_content("hello", cap=2000) == "hello"

    def test_list_content_concatenated(self) -> None:
        content = [
            {"type": "text", "text": "foo"},
            {"type": "text", "text": " bar"},
        ]
        assert _normalize_content(content, cap=2000) == "foo bar"

    def test_list_with_non_text_entries_skipped(self) -> None:
        content = [
            {"type": "text", "text": "keep"},
            {"type": "image", "source": "..."},
        ]
        assert _normalize_content(content, cap=2000) == "keep"

    def test_other_types_return_none(self) -> None:
        assert _normalize_content(42, cap=2000) is None
        assert _normalize_content(None, cap=2000) is None
        assert _normalize_content({}, cap=2000) is None

    def test_tool_result_with_list_content_normalised(self) -> None:
        pairer = ToolPairer()
        pairer.feed(_assistant_line(_tool_use_block("id1", "Read", {"file_path": "/x"})))
        content_list = [{"type": "text", "text": "result data"}]
        calls = pairer.feed(_user_line(_tool_result_block("id1", content_list)))
        assert calls[0].result_preview == "result data"


# ---------------------------------------------------------------------------
# Case 8: inputSummary / resultPreview truncated at cap
# ---------------------------------------------------------------------------

class TestTruncationAtCap:
    """Case 8: inputSummary and resultPreview are truncated at the configured cap."""

    def test_normalize_content_str_truncated(self) -> None:
        long_str = "x" * 100
        result = _normalize_content(long_str, cap=10)
        assert result == "x" * 10

    def test_normalize_content_list_truncated(self) -> None:
        content = [{"type": "text", "text": "a" * 100}]
        result = _normalize_content(content, cap=10)
        assert result == "a" * 10

    def test_summarize_command_truncated(self) -> None:
        long_cmd = "echo " + "a" * 100
        result = _summarize({"command": long_cmd}, cap=10)
        assert result is not None
        assert len(result) <= 10

    def test_input_summary_truncated_in_tool_call(self) -> None:
        pairer = ToolPairer(cap=5)
        long_cmd = "x" * 100
        line = _assistant_line(_tool_use_block("id1", "Bash", {"command": long_cmd}))
        calls = pairer.feed(line)
        assert calls[0].input_summary is not None
        assert len(calls[0].input_summary) <= 5

    def test_result_preview_truncated_in_tool_call(self) -> None:
        pairer = ToolPairer(cap=5)
        pairer.feed(_assistant_line(_tool_use_block("id1", "Read", {"file_path": "/f"})))
        long_result = "y" * 100
        calls = pairer.feed(_user_line(_tool_result_block("id1", long_result)))
        assert calls[0].result_preview is not None
        assert len(calls[0].result_preview) <= 5


# ---------------------------------------------------------------------------
# Case 9: orphan tool_result -> defensive ToolCall
# ---------------------------------------------------------------------------

class TestOrphanToolResult:
    """Case 9: tool_result with unknown tool_use_id emits a defensive ToolCall."""

    def test_orphan_returns_one_call(self) -> None:
        pairer = ToolPairer()
        calls = pairer.feed(_user_line(_tool_result_block("unknown-id", "data")))
        assert len(calls) == 1

    def test_orphan_name_is_empty(self) -> None:
        pairer = ToolPairer()
        calls = pairer.feed(_user_line(_tool_result_block("unknown-id", "data")))
        assert calls[0].name == ""

    def test_orphan_input_summary_is_none(self) -> None:
        pairer = ToolPairer()
        calls = pairer.feed(_user_line(_tool_result_block("unknown-id", "data")))
        assert calls[0].input_summary is None

    def test_orphan_status_ok_when_no_error(self) -> None:
        pairer = ToolPairer()
        calls = pairer.feed(_user_line(_tool_result_block("unknown-id", "data")))
        assert calls[0].status == "ok"

    def test_orphan_status_error_when_is_error(self) -> None:
        pairer = ToolPairer()
        calls = pairer.feed(_user_line(_tool_result_block("unknown-id", "err", is_error=True)))
        assert calls[0].status == "error"

    def test_orphan_tool_use_id_preserved(self) -> None:
        pairer = ToolPairer()
        calls = pairer.feed(_user_line(_tool_result_block("orphan-123", "data")))
        assert calls[0].tool_use_id == "orphan-123"


# ---------------------------------------------------------------------------
# Case 10: eviction beyond max_pending
# ---------------------------------------------------------------------------

class TestToolPairerEviction:
    """Case 10: pending dict is bounded to max_pending; oldest entry is evicted."""

    def test_oldest_evicted_when_limit_reached(self) -> None:
        max_pending = 3
        pairer = ToolPairer(max_pending=max_pending)
        # Fill up to the limit
        for i in range(max_pending):
            pairer.feed(_assistant_line(_tool_use_block(f"id-{i}", "Bash", {"command": "x"})))
        # Add one more -> evicts id-0
        pairer.feed(_assistant_line(_tool_use_block("id-new", "Bash", {"command": "x"})))
        # id-0 result should now be treated as orphan
        calls = pairer.feed(_user_line(_tool_result_block("id-0", "data")))
        assert calls[0].name == ""  # orphan defensive call

    def test_new_entry_still_tracked_after_eviction(self) -> None:
        max_pending = 2
        pairer = ToolPairer(max_pending=max_pending)
        pairer.feed(_assistant_line(_tool_use_block("id-0", "Bash", {"command": "x"})))
        pairer.feed(_assistant_line(_tool_use_block("id-1", "Bash", {"command": "x"})))
        # Adding id-2 evicts id-0
        pairer.feed(_assistant_line(_tool_use_block("id-2", "Read", {"file_path": "/f"})))
        # id-1 and id-2 should still be pending
        calls_1 = pairer.feed(_user_line(_tool_result_block("id-1", "r1")))
        assert calls_1[0].status == "ok"
        assert calls_1[0].name == "Bash"
        calls_2 = pairer.feed(_user_line(_tool_result_block("id-2", "r2")))
        assert calls_2[0].status == "ok"
        assert calls_2[0].name == "Read"

    def test_pending_size_never_exceeds_max(self) -> None:
        max_pending = 5
        pairer = ToolPairer(max_pending=max_pending)
        for i in range(20):
            pairer.feed(_assistant_line(_tool_use_block(f"id-{i}", "Bash", {"command": "x"})))
        # Access public property to verify bound
        assert pairer.pending_count <= max_pending


# ---------------------------------------------------------------------------
# Additional edge cases for _summarize
# ---------------------------------------------------------------------------

class TestSummarize:
    """Edge cases for the _summarize helper."""

    def test_command_key_used_for_bash(self) -> None:
        result = _summarize({"command": "ls -la"}, cap=2000)
        assert result == "ls -la"

    def test_file_path_key_used_for_read(self) -> None:
        result = _summarize({"file_path": "/home/user/file.txt"}, cap=2000)
        assert result == "/home/user/file.txt"

    def test_json_dumps_for_unknown_input(self) -> None:
        result = _summarize({"key": "value"}, cap=2000)
        assert result is not None
        assert "key" in result

    def test_empty_dict_returns_json(self) -> None:
        result = _summarize({}, cap=2000)
        assert result == "{}"

    def test_json_dumps_truncated(self) -> None:
        big = {"key": "v" * 1000}
        result = _summarize(big, cap=10)
        assert result is not None
        assert len(result) <= 10
