"""Pydantic response schemas for orchestration state and timeline (F1/F2).

This module defines the read-only wire schemas for agent topology
(``AgentNode`` / ``AgentTree``, F1) and the agent chronology
(``TimelineEvent``, F2). Report-view schemas (F5) live in a separate module.

All schemas use camelCase JSON aliases via ``alias_generator`` so the wire
format matches the frontend convention (``agentId``, ``startedAt``, …) while
Python code uses snake_case throughout. ``populate_by_name`` allows building
instances from snake_case field names in service code.

Parsing is defensive (see ``app.services.orchestration``): on real data the
agent hierarchy is not reconstructable (``parent_mode`` is always ``"none"``),
so ``AgentTree`` is a flat list ordered by ``started_at`` with
``hierarchy_available`` always ``False``; the replay ``t`` field is unusable
(always ``0``) so timeline timestamps derive from tracking instead.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

Role = Literal["orchestrator", "lead", "worker", "verifier", "unknown"]
Status = Literal["running", "completed", "failed", "blocked", "unknown"]
EventKind = Literal["agent_start", "agent_stop", "agent_complete", "unknown"]


class AgentNode(BaseModel):
    """A single orchestration agent's topology, status and duration (F1).

    ``agent_type`` is normalized (the ``"<plugin>:"`` prefix is stripped).
    ``duration_ms`` is the tracking value when the agent is finished and
    ``None`` while it is running (the API computes the elapsed time).
    ``batch`` is a best-effort temporal batch index (overlapping intervals
    share an index); it is ``None`` when ``started_at`` is unknown.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    agent_id: str
    agent_type: str
    role: Role
    status: Status
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    batch: int | None


class AgentTree(BaseModel):
    """Flat, time-ordered view of a session's agents (F1).

    On real data ``parent_mode`` is always ``"none"``, so no parent/child
    tree is derivable. ``nodes`` is therefore a flat list ordered by
    ``started_at`` and ``hierarchy_available`` is always ``False``; the flag
    documents that limit to consumers.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    session_id: str
    nodes: list[AgentNode]
    hierarchy_available: bool


class TimelineEvent(BaseModel):
    """A single chronology entry for the session timeline (F2).

    ``at`` derives from tracking ``started_at`` / ``completed_at`` (the replay
    ``t`` field is always ``0`` and thus unusable). ``agent_id`` is the full
    tracking id matched by prefix from the replay's short ``agent`` field, or
    ``None`` when no match exists. ``success`` is present only on stops.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    agent_id: str | None
    agent_type: str
    event: EventKind
    success: bool | None
    at: datetime | None
    duration_ms: int | None
