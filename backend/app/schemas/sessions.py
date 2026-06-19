"""Pydantic response schemas for session discovery endpoints.

``SessionSummary`` is the list-view schema (GET /api/sessions).
``SessionDetail`` is the detail-view schema (GET /api/sessions/{sessionId}).

Both use camelCase JSON aliases via ``alias_generator`` so the wire format
matches the frontend convention (``sessionId``, ``lastActivity``, …) while
Python code uses snake_case throughout.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class SessionSummary(BaseModel):
    """Summary view of a discovered Claude Code session.

    Returned as list items by ``GET /api/sessions``.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    session_id: str
    project_path: str
    project_slug: str
    resolved: bool
    title: str | None
    state: Literal["active", "recent", "terminated"]
    last_activity: datetime | None
    started_at: datetime | None


class SessionDetail(SessionSummary):
    """Full detail view of a single Claude Code session.

    Returned by ``GET /api/sessions/{sessionId}``. Extends
    :class:`SessionSummary` with fields that require reading the transcript
    or the per-project state directory.
    """

    transcript_path: str | None
    has_state: bool
    pid: int | None
    event_count: int | None
