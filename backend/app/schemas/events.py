"""Pydantic schemas for SSE event payloads and SSE encoder (W3, Increment 3).

Defines all wire-format models for the ``GET /api/sessions/{sid}/events``
stream.  Every model uses ``alias_generator=to_camel`` so JSON keys are
camelCase while Python code uses snake_case throughout.  ``populate_by_name``
allows constructing instances from snake_case kwargs in service code.

Public types consumed by W2 (``live_parser``) and W4 (``streamer``):
  - ``ToolStatus``, ``BlockKind``   — Literal type aliases
  - ``ReasoningBlock``              — F3 thinking/text/redacted_thinking
  - ``ToolCall``                    — F4 tool invocation + result
  - ``AgentBlocksEvent``            — SSE event wrapping a list of blocks
  - ``ToolActivityEvent``           — SSE event wrapping a list of tool calls
  - ``TimelineEventBatch``          — SSE event wrapping timeline entries
  - ``ReportAvailableEvent``        — SSE event for new report files
  - ``HeartbeatEvent``              — periodic keep-alive
  - ``encode_sse``                  — serialises a model into an SSE message
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from app.schemas.orchestration import TimelineEvent

# ---------------------------------------------------------------------------
# Literal type aliases (exported for W2 / W4)
# ---------------------------------------------------------------------------

ToolStatus = Literal["running", "ok", "error"]
"""Lifecycle status of a single tool invocation."""

BlockKind = Literal["thinking", "text", "redacted_thinking"]
"""Kind of a reasoning block emitted by an agent."""


# ---------------------------------------------------------------------------
# Sub-models (components shared across SSE events)
# ---------------------------------------------------------------------------

class ReasoningBlock(BaseModel):
    """A single reasoning or text block from an agent transcript (F3).

    ``kind`` is one of ``"thinking"``, ``"text"``, or ``"redacted_thinking"``.
    For ``redacted_thinking`` the ``text`` field is always ``None`` — the
    content is masked and must never be invented.  ``agent_id`` is ``None``
    for the orchestrator transcript; non-``None`` for a sub-agent.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    kind: BlockKind
    text: str | None = None
    agent_id: str | None = None
    ts: datetime | None = None


class ToolCall(BaseModel):
    """A single tool invocation and its eventual result (F4).

    ``tool_use_id`` is the ``id`` field from the ``tool_use`` transcript block
    and the ``tool_use_id`` field from the paired ``tool_result`` block.
    ``status`` progresses from ``"running"`` (on first sighting of the
    ``tool_use``) to ``"ok"`` or ``"error"`` (when the ``tool_result`` arrives).
    ``input_summary`` and ``result_preview`` are truncated to the configured
    cap to bound memory usage on large live transcripts.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    tool_use_id: str
    name: str
    input_summary: str | None = None
    status: ToolStatus
    result_preview: str | None = None
    agent_id: str | None = None
    ts: datetime | None = None


# ---------------------------------------------------------------------------
# SSE event payload models
# ---------------------------------------------------------------------------

class AgentBlocksEvent(BaseModel):
    """SSE ``agent_blocks`` event payload.

    Emitted when new ``ReasoningBlock`` items are extracted from the
    orchestrator transcript (``agent_id=None``) or a sub-agent transcript.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    session_id: str
    agent_id: str | None = None
    blocks: list[ReasoningBlock]


class ToolActivityEvent(BaseModel):
    """SSE ``tool_activity`` event payload.

    Emitted when new ``ToolCall`` items are extracted from any transcript
    (orchestrator or sub-agent).  Each call carries its own ``agent_id``.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    session_id: str
    agent_id: str | None = None
    calls: list[ToolCall]


class TimelineEventBatch(BaseModel):
    """SSE ``timeline`` event payload.

    Wraps the list of ``TimelineEvent`` instances produced by
    ``build_timeline`` (``app.services.orchestration``).
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    session_id: str
    events: list[TimelineEvent]


class ReportAvailableEvent(BaseModel):
    """SSE ``report_available`` event payload.

    Emitted when a new ``reports/*__<role>.md`` file is detected in the
    session's ``.omc/reports/`` directory.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    session_id: str
    role: str
    file_name: str


class HeartbeatEvent(BaseModel):
    """SSE ``heartbeat`` event payload.

    Emitted periodically (default every 15 s) when no other event has been
    sent, so the client can detect stalled connections.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    session_id: str
    ts: datetime


# ---------------------------------------------------------------------------
# SSE encoder
# ---------------------------------------------------------------------------

def encode_sse(event: str, model: BaseModel) -> str:
    """Encode a Pydantic model as an SSE message string.

    Returns a string of the form::

        event: <event>\\n
        data: <json>\\n
        \\n

    The JSON payload uses camelCase aliases (``by_alias=True``) to match the
    frontend convention.  The double trailing newline is the SSE message
    terminator required by the specification.

    Args:
        event: The SSE event name (e.g. ``"heartbeat"``, ``"agent_blocks"``).
        model: A Pydantic ``BaseModel`` instance to serialise as the data field.

    Returns:
        A complete SSE message string ready to write to the response stream.
    """
    return f"event: {event}\ndata: {model.model_dump_json(by_alias=True)}\n\n"
