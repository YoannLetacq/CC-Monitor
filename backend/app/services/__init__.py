"""Service layer package for CC-monitor backend business logic."""

from app.services.discovery import (
    SessionRecord,
    classify_state,
    compute_last_activity,
    count_events,
    discover_sessions,
    read_last_ai_title,
    read_session_started,
)
from app.services.resolver import path_to_slug, resolve_project, slug_to_path

__all__ = [
    "SessionRecord",
    "classify_state",
    "compute_last_activity",
    "count_events",
    "discover_sessions",
    "read_last_ai_title",
    "read_session_started",
    "path_to_slug",
    "resolve_project",
    "slug_to_path",
]
