"""Read-only tmux endpoints: pane discovery and live pane SSE.

Routes (API CONTRACT, brief):
    GET /api/tmux/sessions           → nested session/window/pane tree
    GET /api/tmux/panes/{id}/events  → SSE snapshot/delta/heartbeat/pane_closed

Security (AGENT_CONDUCT §1.4): ``pane_id`` is validated as digits and by
membership in the enumerated pane list before any capture starts; it is
never interpolated into a shell string (the service uses list argv only).
Without a tmux server the discovery route returns an empty list and the
stream route 404 — never a 500 (graceful no-tmux).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.core.config import Settings, get_settings
from app.schemas.tmux import TmuxSession
from app.services.tmux_source import (
    list_tmux_sessions,
    pane_event_generator,
    pane_exists,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tmux", tags=["tmux"])

_SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


@router.get("/sessions", response_model=list[TmuxSession])
def get_tmux_sessions() -> list[TmuxSession]:
    """List tmux sessions/windows/panes; empty list without a tmux server."""
    return list_tmux_sessions()


@router.get("/panes/{pane_id}/events")
def stream_pane_events(
    pane_id: str,
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Stream a pane's live content as SSE; 404 for an unknown pane."""
    if not pane_id.isdigit() or not pane_exists(pane_id):
        logger.debug("tmux: pane %r not found", pane_id)
        raise HTTPException(status_code=404, detail="Pane not found")
    return StreamingResponse(
        pane_event_generator(pane_id, settings),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
