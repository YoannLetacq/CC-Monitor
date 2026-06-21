"""REST endpoints for orchestration state, timeline, reports and overview.

Routes (§1.4):
    GET /api/sessions/{sid}/agents     → AgentTree           (F1)
    GET /api/sessions/{sid}/timeline   → list[TimelineEvent] (F2)
    GET /api/sessions/{sid}/reports    → list[ReportView]    (F5, all)
    GET /api/sessions/{sid}/reports/{role} → ReportView      (F5, one)
    GET /api/sessions/{sid}/overview   → SessionOverview     (F6)

Security (§1.5 / AGENT_CONDUCT §1.4):
    * ``sid`` is validated exclusively by membership in the enumerated session
      list (``discover_sessions``).  It is **never** used to build a file path
      directly (CWE-22 path-traversal prevention).
    * ``role`` is whitelisted to ``^[A-Za-z0-9_-]+$``; a non-matching value
      yields ``available=False`` without raising, or HTTP 422.
    * ``state_dir`` and ``reports_dir`` are derived from the resolved
      ``project_path``; they do not incorporate raw user input.

All endpoints are read-only; no file is opened in write mode.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.core.config import Settings, get_settings
from app.schemas.orchestration import AgentTree, SessionOverview, TimelineEvent
from app.schemas.reports import ReportView
from app.services.discovery import discover_sessions
from app.services.orchestration import build_agent_tree, build_timeline
from app.services.overview import build_overview
from app.services.reports import load_all_reports, load_report

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["orchestration"])


def _get_projects_root(settings: Settings) -> Path:
    """Resolve the projects root path, expanding ``~``."""
    return Path(settings.claude_projects_root).expanduser()


def _resolve_session(sid: str, settings: Settings) -> str:
    """Return the project_path for ``sid``, raising HTTP 404 if unknown.

    ``sid`` is validated by membership in the enumerated session list and is
    **never** used to build a file path directly (path-traversal prevention).
    """
    projects_root = _get_projects_root(settings)
    records = discover_sessions(projects_root)
    for record in records:
        if record.session_id == sid:
            return record.project_path
    logger.debug("orchestration: session_id %r not found", sid)
    raise HTTPException(status_code=404, detail="Session not found")


def _dirs(project_path: str) -> tuple[Path, Path]:
    """Return ``(state_dir, reports_dir)`` derived from a resolved project path."""
    root = Path(project_path)
    return root / ".omc" / "state", root / ".omc" / "reports"


@router.get("/sessions/{sid}/agents", response_model=AgentTree)
def get_agents(
    sid: str,
    settings: Settings = Depends(get_settings),
) -> AgentTree:
    """Return the flat, time-ordered agent list for the session (F1).

    ``hierarchy_available`` is always ``False`` on real data (no parent/child
    link exists). ``sid`` is validated by enumeration — never used as a path.
    """
    project_path = _resolve_session(sid, settings)
    state_dir, _ = _dirs(project_path)
    return build_agent_tree(state_dir, sid)


@router.get("/sessions/{sid}/timeline", response_model=list[TimelineEvent])
def get_timeline(
    sid: str,
    settings: Settings = Depends(get_settings),
) -> list[TimelineEvent]:
    """Return the chronological agent event list for the session (F2).

    Timestamps come from ``subagent-tracking.json`` (the replay ``t`` field is
    always ``0`` and unusable). ``sid`` is validated by enumeration.
    """
    project_path = _resolve_session(sid, settings)
    state_dir, _ = _dirs(project_path)
    return build_timeline(state_dir, sid)


@router.get("/sessions/{sid}/reports", response_model=list[ReportView])
def get_reports(
    sid: str,
    settings: Settings = Depends(get_settings),
) -> list[ReportView]:
    """Return all parsed reports for the session (F5).

    ``sid`` is validated by enumeration; reports are read from the resolved
    project's ``reports`` directory.
    """
    project_path = _resolve_session(sid, settings)
    _, reports_dir = _dirs(project_path)
    return load_all_reports(reports_dir)


@router.get("/sessions/{sid}/reports/{role}", response_model=ReportView)
def get_report_by_role(
    sid: str,
    role: str,
    settings: Settings = Depends(get_settings),
) -> ReportView:
    """Return the report for ``role``, or ``available=False`` if absent (F5).

    ``role`` is whitelisted inside ``load_report`` to ``^[A-Za-z0-9_-]+$``
    (CWE-22); a non-matching value yields ``available=False``. HTTP 404 is
    only raised when ``sid`` is unknown — a plausible but unwritten report
    returns 200 with ``available=False``.
    """
    project_path = _resolve_session(sid, settings)
    _, reports_dir = _dirs(project_path)
    return load_report(reports_dir, role)


@router.get("/sessions/{sid}/overview", response_model=SessionOverview)
def get_overview(
    sid: str,
    settings: Settings = Depends(get_settings),
) -> SessionOverview:
    """Return the session overview with recomputed KPIs (F6).

    ``active``/``done``/``failed``/``queued`` are recounted from ``agents[]``;
    the incoherent ``total_*`` aggregates are ignored. ``gate_summary``
    aggregates PASS/FAIL across all available reports.
    """
    project_path = _resolve_session(sid, settings)
    state_dir, reports_dir = _dirs(project_path)
    return build_overview(state_dir, reports_dir, sid)
