"""SSE endpoint for live session streaming (W4, Increment 3).

Route (§1.1):
    GET /api/sessions/{sid}/events → text/event-stream (snapshot + deltas)

Security (§1.8 / AGENT_CONDUCT §1.4):
    * ``sid`` is validated exclusively by membership in the enumerated session
      list (``discover_sessions``); it is **never** used to build a file path
      directly (CWE-22 path-traversal prevention).
    * Only the **active** session may be streamed: a non-active session yields
      HTTP 409, an unknown session HTTP 404.

The response is read-only; no file is opened in write mode.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.core.config import Settings, get_settings
from app.services.discovery import SessionRecord, classify_state, discover_sessions
from app.services.streamer import event_generator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["events"])

_SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


def _get_projects_root(settings: Settings) -> Path:
    """Resolve the projects root path, expanding ``~``."""
    return Path(settings.claude_projects_root).expanduser()


def _resolve_record(sid: str, settings: Settings) -> SessionRecord:
    """Return the session record for ``sid``, raising HTTP 404 if unknown.

    ``sid`` is validated by membership in the enumerated session list and is
    **never** used to build a file path directly (path-traversal prevention).
    """
    projects_root = _get_projects_root(settings)
    for record in discover_sessions(projects_root):
        if record.session_id == sid:
            return record
    logger.debug("events: session_id %r not found", sid)
    raise HTTPException(status_code=404, detail="Session not found")


def _require_active(record: SessionRecord, settings: Settings) -> None:
    """Raise HTTP 409 when the session is not currently ``active`` (§1.1)."""
    state = classify_state(
        record.last_activity,
        datetime.now(timezone.utc),
        settings.session_active_threshold_s,
        settings.session_recent_threshold_s,
    )
    if state != "active":
        logger.debug("events: session %r not live (state=%s)", record.session_id, state)
        raise HTTPException(status_code=409, detail="Session not live")


@router.get("/sessions/{sid}/events")
def stream_events(
    sid: str,
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Stream live session events as Server-Sent Events (§1.1).

    Resolves ``sid`` by enumeration (404 if unknown), refuses a non-active
    session (409), and otherwise returns a ``text/event-stream`` response whose
    body is the snapshot-then-deltas generator.  ``sid`` is never used to build
    a path directly.
    """
    record = _resolve_record(sid, settings)
    _require_active(record, settings)
    if record.transcript_path is None:
        logger.debug("events: session %r has no transcript", sid)
        raise HTTPException(status_code=409, detail="Session not live")
    generator = event_generator(
        record.project_path,
        sid,
        record.transcript_path,
        settings,
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
