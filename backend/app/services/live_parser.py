"""Live parsing of transcript lines into ReasoningBlocks and ToolCalls (W2, Increment 3).

Transforms already-parsed transcript dicts (from the tailer) into typed
objects consumed by the SSE streamer (W4).  This module is deliberately
pure: no file I/O, no async — inputs are plain ``list[dict]`` values.

Public API
----------
``extract_reasoning(line)``
    Extract ``ReasoningBlock`` instances from a single assistant line.

``ToolPairer``
    Stateful class that matches ``tool_use`` blocks with their
    ``tool_result`` counterparts and emits ``ToolCall`` instances.

Internal helpers
----------------
``_normalize_content(content, cap)``
    Normalise ``tool_result`` content (str or list) to a capped string.

``_summarize(input_dict, cap)``
    Produce a best-effort summary of a ``tool_use`` input dict.
"""

import json
import logging
from collections import OrderedDict
from datetime import datetime

from app.schemas.events import BlockKind, ReasoningBlock, ToolCall, ToolStatus

logger = logging.getLogger(__name__)

_DEFAULT_CAP: int = 2000
_DEFAULT_MAX_PENDING: int = 512

# Map raw transcript block types to BlockKind values (only the three we handle).
_BLOCK_KIND_MAP: dict[str, BlockKind] = {
    "thinking": "thinking",
    "text": "text",
    "redacted_thinking": "redacted_thinking",
}


def _parse_ts(raw: str) -> datetime | None:
    """Parse an ISO-8601 timestamp string, normalising a trailing ``Z`` to UTC.

    Duplicates the logic of ``discovery._parse_timestamp`` locally to avoid
    importing a private symbol from another module.

    Args:
        raw: An ISO-8601 timestamp string, possibly ending with ``Z``.

    Returns:
        A ``datetime`` instance, or ``None`` if parsing fails.
    """
    normalised: str = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        return datetime.fromisoformat(normalised)
    except ValueError as exc:
        logger.debug("_parse_ts: unparseable timestamp %r: %s", raw, exc)
        return None


def extract_reasoning(line: dict) -> list[ReasoningBlock]:
    """Extract ReasoningBlock items from a single assistant transcript line.

    Only lines with ``type == "assistant"`` carry reasoning blocks; all other
    line types yield an empty list.  Within ``message.content``, only blocks
    whose ``type`` is one of ``"thinking"``, ``"text"``, or
    ``"redacted_thinking"`` are yielded; every other block type
    (``"tool_use"``, ``"server_tool_use"``, unknown) is silently ignored.

    Args:
        line: A parsed transcript line dict (top-level object from a JSONL).

    Returns:
        A (possibly empty) list of ``ReasoningBlock`` instances.
    """
    if line.get("type") != "assistant":
        return []

    message = line.get("message")
    if not isinstance(message, dict):
        return []

    content = message.get("content")
    if not isinstance(content, list):
        return []

    agent_id: str | None = line.get("agentId")
    raw_ts: object = line.get("timestamp")
    ts = _parse_ts(raw_ts) if isinstance(raw_ts, str) else None

    blocks: list[ReasoningBlock] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type: str = block.get("type", "")
        kind: BlockKind | None = _BLOCK_KIND_MAP.get(block_type)
        if kind is None:
            logger.debug("extract_reasoning: ignoring block type %r", block_type)
            continue
        text: str | None = _extract_block_text(block, block_type)
        blocks.append(ReasoningBlock(kind=kind, text=text, agent_id=agent_id, ts=ts))

    return blocks


def _extract_block_text(block: dict, block_type: str) -> str | None:
    """Return the text payload for a reasoning block, or None for redacted.

    Args:
        block: The content block dict.
        block_type: The ``type`` field of the block.

    Returns:
        The text string for ``"thinking"`` and ``"text"`` blocks; ``None``
        for ``"redacted_thinking"`` (content is masked by the runtime).
    """
    if block_type == "thinking":
        raw = block.get("thinking")
        return raw if isinstance(raw, str) else None
    if block_type == "text":
        raw = block.get("text")
        return raw if isinstance(raw, str) else None
    # redacted_thinking: text is always None — never invented.
    return None


def _normalize_content(content: object, cap: int) -> str | None:
    """Normalise tool_result content to a capped string, or None.

    Handles the two formats observed in real transcripts (§0.2):

    * ``str`` — returned directly, truncated to ``cap`` characters.
    * ``list`` of ``{type: "text", text: "…"}`` — text values are
      concatenated then truncated to ``cap`` characters.
    * Any other type — returns ``None`` (defensive).

    Args:
        content: The ``content`` field from a ``tool_result`` block.
        cap: Maximum number of characters in the returned string.

    Returns:
        A string (possibly truncated) or ``None``.
    """
    if isinstance(content, str):
        return content[:cap]
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                raw_text = item.get("text")
                if isinstance(raw_text, str):
                    parts.append(raw_text)
        return "".join(parts)[:cap]
    return None


def _summarize(input_dict: dict, cap: int) -> str | None:
    """Produce a best-effort summary of a tool_use input dict.

    Priority order (matches the most common Claude Code tools):
    1. ``command`` key — Bash tool.
    2. ``file_path`` key — Read / Edit / Write tools.
    3. Fallback to a JSON-serialised representation, truncated to ``cap``.

    Args:
        input_dict: The ``input`` field from a ``tool_use`` block.
        cap: Maximum number of characters in the returned string.

    Returns:
        A summary string truncated to ``cap`` characters, or ``None`` if the
        dict is not serialisable (practically never with real transcripts).
    """
    if "command" in input_dict:
        raw = input_dict["command"]
        if isinstance(raw, str):
            return raw[:cap]
    if "file_path" in input_dict:
        raw = input_dict["file_path"]
        if isinstance(raw, str):
            return raw[:cap]
    try:
        return json.dumps(input_dict)[:cap]
    except (TypeError, ValueError) as exc:
        logger.debug("_summarize: cannot serialise input: %s", exc)
        return None


class ToolPairer:
    """Stateful matcher for tool_use / tool_result transcript block pairs (F4).

    Maintains a bounded ordered dict of pending ``ToolCall`` instances keyed
    by ``tool_use_id``.  When a ``tool_use`` block is seen, a ``running``
    call is stored and emitted.  When the matching ``tool_result`` arrives,
    the call is completed (``ok`` or ``error``) and removed from the pending
    dict.  An orphan ``tool_result`` (no matching ``tool_use``) emits a
    defensive ``ToolCall`` with ``name=""`` and ``input_summary=None``.

    The pending dict is bounded by ``max_pending``; when the limit is
    exceeded the **oldest** entry is evicted (FIFO via ``OrderedDict``).

    Args:
        max_pending: Maximum number of unresolved tool_use entries kept in
            memory at once.  Defaults to 512.
        cap: Character cap applied to ``input_summary`` and
            ``result_preview``.  Defaults to 2000.
    """

    def __init__(
        self,
        max_pending: int = _DEFAULT_MAX_PENDING,
        cap: int = _DEFAULT_CAP,
    ) -> None:
        """Initialise the pairer with given bounds."""
        self._pending: OrderedDict[str, ToolCall] = OrderedDict()
        self._max_pending: int = max_pending
        self._cap: int = cap

    @property
    def pending_count(self) -> int:
        """Return the number of tool_use entries currently awaiting a result."""
        return len(self._pending)

    def feed(self, line: dict) -> list[ToolCall]:
        """Process a single transcript line and emit any completed ToolCalls.

        Scans the ``message.content`` list of the line for ``tool_use``
        blocks (expected on ``assistant`` lines) and ``tool_result`` blocks
        (expected on ``user`` lines).  Each emitted ``ToolCall`` is
        immediately ready for the SSE stream.

        Args:
            line: A parsed transcript line dict.

        Returns:
            A (possibly empty) list of ``ToolCall`` instances emitted by
            this line.
        """
        message = line.get("message")
        if not isinstance(message, dict):
            return []

        content = message.get("content")
        if not isinstance(content, list):
            return []

        raw_ts: object = line.get("timestamp")
        ts = _parse_ts(raw_ts) if isinstance(raw_ts, str) else None
        agent_id: str | None = line.get("agentId")

        emitted: list[ToolCall] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "tool_use":
                call = self._handle_tool_use(block, agent_id, ts)
                emitted.append(call)
            elif block_type == "tool_result":
                call = self._handle_tool_result(block, agent_id, ts)
                emitted.append(call)
        return emitted

    def _handle_tool_use(
        self,
        block: dict,
        agent_id: str | None,
        ts: datetime | None,
    ) -> ToolCall:
        """Register a tool_use block as a running ToolCall.

        Evicts the oldest pending entry when ``max_pending`` is exceeded.

        Args:
            block: The ``tool_use`` content block dict.
            agent_id: Agent identifier from the transcript line, or None.
            ts: Parsed timestamp from the transcript line, or None.

        Returns:
            The newly created ``ToolCall`` with ``status="running"``.
        """
        tool_use_id: str = block.get("id", "")
        name: str = block.get("name", "")
        raw_input: object = block.get("input", {})
        input_dict: dict = raw_input if isinstance(raw_input, dict) else {}

        call = ToolCall(
            tool_use_id=tool_use_id,
            name=name,
            input_summary=_summarize(input_dict, self._cap),
            status="running",
            result_preview=None,
            agent_id=agent_id,
            ts=ts,
        )

        if len(self._pending) >= self._max_pending:
            oldest_key = next(iter(self._pending))
            self._pending.pop(oldest_key)
            logger.debug("ToolPairer: evicted oldest pending tool_use_id=%r", oldest_key)

        self._pending[tool_use_id] = call
        return call

    def _handle_tool_result(
        self,
        block: dict,
        agent_id: str | None,
        ts: datetime | None,
    ) -> ToolCall:
        """Match a tool_result block with its pending tool_use, or emit defensively.

        Args:
            block: The ``tool_result`` content block dict.
            agent_id: Agent identifier from the transcript line, or None.
            ts: Parsed timestamp from the transcript line, or None.

        Returns:
            The completed ``ToolCall`` (``status="ok"`` or ``"error"``).
        """
        tool_use_id: str = block.get("tool_use_id", "")
        is_error: bool = bool(block.get("is_error", False))
        content: object = block.get("content", None)
        result_preview: str | None = _normalize_content(content, self._cap)
        status: ToolStatus = "error" if is_error else "ok"

        pending_call: ToolCall | None = self._pending.pop(tool_use_id, None)

        if pending_call is not None:
            return ToolCall(
                tool_use_id=tool_use_id,
                name=pending_call.name,
                input_summary=pending_call.input_summary,
                status=status,
                result_preview=result_preview,
                agent_id=agent_id if agent_id is not None else pending_call.agent_id,
                ts=ts if ts is not None else pending_call.ts,
            )

        # Orphan tool_result: emit a defensive call with empty name.
        logger.debug("ToolPairer: orphan tool_result tool_use_id=%r", tool_use_id)
        return ToolCall(
            tool_use_id=tool_use_id,
            name="",
            input_summary=None,
            status=status,
            result_preview=result_preview,
            agent_id=agent_id,
            ts=ts,
        )
