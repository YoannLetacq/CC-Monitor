"""REST endpoints for Claude Code session discovery.

Routes
------
GET /api/sessions
    List all discovered sessions, sorted by ``last_activity`` descending
    (most recent first, ``null`` last). Accepts an optional ``sort`` query
    parameter (``"recent"`` or ``"oldest"``; default ``"recent"``).

GET /api/sessions/{session_id}
    Return the full detail for one session. Responds with HTTP 404 if
    ``session_id`` is not present in the enumerated set — the raw URL
    parameter is **never** used to build a file path directly, which
    prevents path-traversal attacks (AGENT_CONDUCT §1.4, INCR1_DISPATCH §1.6).
"""

import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from app.core.config import Settings, get_settings
from app.schemas.sessions import SessionDetail, SessionSummary
from app.services.discovery import (
    SessionRecord,
    _classify_for_record,
    discover_sessions,
    find_session,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["sessions"])

_SortParam = Literal["recent", "oldest"]

_SORT_RECENT: str = "recent"


def _to_summary(record: SessionRecord, settings: Settings) -> SessionSummary:
    """Map a :class:`SessionRecord` to a :class:`SessionSummary` response model."""
    return SessionSummary(
        session_id=record.session_id,
        project_path=record.project_path,
        project_slug=record.project_slug,
        resolved=record.resolved,
        title=record.title,
        state=_classify_for_record(record, settings),
        last_activity=record.last_activity,
        started_at=record.started_at,
    )


def _to_detail(record: SessionRecord, settings: Settings) -> SessionDetail:
    """Map a :class:`SessionRecord` to a :class:`SessionDetail` response model."""
    transcript_path = (
        str(record.transcript_path) if record.transcript_path is not None else None
    )
    return SessionDetail(
        session_id=record.session_id,
        project_path=record.project_path,
        project_slug=record.project_slug,
        resolved=record.resolved,
        title=record.title,
        state=_classify_for_record(record, settings),
        last_activity=record.last_activity,
        started_at=record.started_at,
        transcript_path=transcript_path,
        has_state=record.has_state,
        pid=record.pid,
        event_count=record.event_count,
    )


def _get_projects_root(settings: Settings) -> Path:
    """Resolve the projects root path, expanding ``~``."""
    return Path(settings.claude_projects_root).expanduser()


@router.get("/sessions", response_model=list[SessionSummary])
def list_sessions(
    sort: _SortParam = _SORT_RECENT,
    settings: Settings = Depends(get_settings),
) -> list[SessionSummary]:
    """Return all discovered sessions, sorted by ``lastActivity``.

    The default sort order is ``recent`` (most recent first, ``null`` last).
    Pass ``sort=oldest`` to reverse.
    """
    projects_root = _get_projects_root(settings)
    records = discover_sessions(projects_root)
    summaries = [_to_summary(r, settings) for r in records]
    reverse = sort == _SORT_RECENT
    non_null = sorted(
        (s for s in summaries if s.last_activity is not None),
        key=lambda s: s.last_activity,
        reverse=reverse,
    )
    nulls = [s for s in summaries if s.last_activity is None]
    summaries = non_null + nulls
    logger.debug("list_sessions: returning %s sessions (sort=%s)", len(summaries), sort)
    return summaries


@router.get("/sessions/{session_id}", response_model=SessionDetail)
def get_session(
    session_id: str,
    settings: Settings = Depends(get_settings),
) -> SessionDetail:
    """Return the full detail for a single session.

    The ``session_id`` path parameter is validated exclusively by membership
    in the enumerated session list — it is **never** used to construct a file
    path directly. An unrecognised id (including path-traversal payloads)
    yields HTTP 404.

    Uses ``find_session`` which short-circuits enumeration on first match
    (PERF2), preserving the path-traversal protection: ``session_id`` is only
    compared against ids produced by the enumeration, never used to build a
    file path.
    """
    projects_root = _get_projects_root(settings)
    record = find_session(projects_root, session_id)
    if record is not None:
        return _to_detail(record, settings)
    logger.debug("get_session: session_id %r not found", session_id)
    raise HTTPException(status_code=404, detail="Session not found")
