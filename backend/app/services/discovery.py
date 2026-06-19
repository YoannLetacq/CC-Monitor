"""Read-only discovery of Claude Code sessions from transcripts and state.

This module enumerates sessions by unioning two independent on-disk sources:

* transcript files ``<projects_root>/<slug>/<sessionId>.jsonl`` (D3); and
* per-project state directories
  ``<project>/.omc/state/sessions/<sessionId>/session-started.json`` (D6),
  reached through the project path resolved from the transcript slug or an
  authoritative ``cwd``.

Either source may be missing for a given session (D7). All file access is
read-only; parsing is defensive so corrupt lines or malformed JSON never
crash discovery (they are skipped and logged at debug level).
"""

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.services.resolver import resolve_project, slug_to_path

logger = logging.getLogger(__name__)

_TIMESTAMP_TYPES: frozenset[str] = frozenset(
    {"assistant", "user", "system", "attachment", "queue-operation"}
)

_STATE_SUBPATH: tuple[str, ...] = (".omc", "state", "sessions")


@dataclass(frozen=True)
class SessionRecord:
    """Discovered session, unioning transcript and state sources.

    Attributes mirror the cheap-to-read facts available without parsing
    transcript content blocks. Optional fields are ``None`` when their
    backing source is absent (D7).
    """

    session_id: str
    project_slug: str
    project_path: str
    resolved: bool
    transcript_path: Path | None
    has_state: bool
    started_at: datetime | None
    pid: int | None
    title: str | None
    last_activity: datetime | None
    event_count: int | None


def _parse_timestamp(raw: str) -> datetime | None:
    """Parse an ISO-8601 timestamp, normalising a trailing ``Z`` to UTC."""
    normalised: str = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        return datetime.fromisoformat(normalised)
    except ValueError as exc:
        logger.debug("unparseable timestamp %r: %s", raw, exc)
        return None


def _iter_json_lines(transcript_path: Path) -> Iterator[dict]:
    """Yield parsed JSON objects from a transcript, skipping corrupt lines."""
    try:
        with open(transcript_path, "r", encoding="utf-8") as handle:
            for line in handle:
                stripped: str = line.strip()
                if not stripped:
                    continue
                try:
                    yield json.loads(stripped)
                except json.JSONDecodeError as exc:
                    logger.debug("skipping corrupt line in %s: %s", transcript_path, exc)
    except OSError as exc:
        logger.debug("cannot read transcript %s: %s", transcript_path, exc)


def read_transcript_cwd(transcript_path: Path) -> str | None:
    """Return the first ``cwd`` recorded in a transcript line, else ``None``.

    Timestamped transcript lines carry the authoritative working directory of
    the session. This recovers the true project path even when the directory
    slug is lossy (a real directory name may contain dashes).
    """
    for obj in _iter_json_lines(transcript_path):
        if isinstance(obj, dict):
            cwd = obj.get("cwd")
            if isinstance(cwd, str) and cwd:
                return cwd
    return None


def read_last_ai_title(transcript_path: Path) -> str | None:
    """Return the ``aiTitle`` of the last ``ai-title`` event, else ``None``."""
    title: str | None = None
    for obj in _iter_json_lines(transcript_path):
        if isinstance(obj, dict) and obj.get("type") == "ai-title":
            value = obj.get("aiTitle")
            if isinstance(value, str):
                title = value
    return title


def compute_last_activity(transcript_path: Path) -> datetime | None:
    """Return the max parseable timestamp, falling back to the file mtime.

    Returns ``None`` only when the file is missing or unreadable.
    """
    latest: datetime | None = None
    for obj in _iter_json_lines(transcript_path):
        if not isinstance(obj, dict) or obj.get("type") not in _TIMESTAMP_TYPES:
            continue
        raw = obj.get("timestamp")
        if not isinstance(raw, str):
            continue
        parsed = _parse_timestamp(raw)
        if parsed is not None and (latest is None or parsed > latest):
            latest = parsed
    if latest is not None:
        return latest
    return _mtime(transcript_path)


def _mtime(transcript_path: Path) -> datetime | None:
    """Return the file modification time as a UTC datetime, else ``None``."""
    try:
        stamp = transcript_path.stat().st_mtime
    except OSError as exc:
        logger.debug("cannot stat %s: %s", transcript_path, exc)
        return None
    return datetime.fromtimestamp(stamp, tz=timezone.utc)


def count_events(transcript_path: Path) -> int:
    """Count parseable JSON lines in the transcript (corrupt lines skipped)."""
    return sum(1 for _ in _iter_json_lines(transcript_path))


def read_session_started(project_root: Path, session_id: str) -> dict | None:
    """Read ``session-started.json`` for a session, tolerating absence.

    A missing or malformed file is treated as absent and yields ``None``.
    """
    state_file: Path = project_root.joinpath(*_STATE_SUBPATH, session_id, "session-started.json")
    try:
        with open(state_file, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.debug("no usable session-started for %s: %s", session_id, exc)
        return None
    return data if isinstance(data, dict) else None


def classify_state(
    last_activity: datetime | None,
    now: datetime,
    active_s: int,
    recent_s: int,
) -> str:
    """Classify a session temporally; ``now`` is injected for determinism.

    A missing ``last_activity`` is a safe ``terminated``.
    """
    if last_activity is None:
        return "terminated"
    delta: float = (now - last_activity).total_seconds()
    if delta < active_s:
        return "active"
    if delta < recent_s:
        return "recent"
    return "terminated"


def _list_state_session_ids(project_root: Path) -> set[str]:
    """List session ids present under the project's state directory."""
    state_dir: Path = project_root.joinpath(*_STATE_SUBPATH)
    try:
        return {child.name for child in state_dir.iterdir() if child.is_dir()}
    except OSError as exc:
        logger.debug("no state directory at %s: %s", state_dir, exc)
        return set()


def _list_transcripts(slug_dir: Path) -> dict[str, Path]:
    """Map session ids to transcript paths for one slug directory."""
    try:
        return {f.stem: f for f in slug_dir.glob("*.jsonl") if f.is_file()}
    except OSError as exc:
        logger.debug("cannot list transcripts in %s: %s", slug_dir, exc)
        return {}


def _build_record(
    session_id: str,
    slug: str,
    transcript_path: Path | None,
    state: dict | None,
) -> SessionRecord:
    """Assemble a :class:`SessionRecord` from its resolved sources.

    The authoritative ``cwd`` comes from ``session-started.json`` when present,
    else from the transcript itself; both recover the true project path even
    when the slug is lossy.
    """
    cwd = _authoritative_cwd(state, transcript_path)
    project_path, resolved = resolve_project(slug, cwd)
    title = read_last_ai_title(transcript_path) if transcript_path is not None else None
    last_activity = (
        compute_last_activity(transcript_path) if transcript_path is not None else None
    )
    event_count = count_events(transcript_path) if transcript_path is not None else None
    pid = state.get("pid") if state is not None else None
    return SessionRecord(
        session_id=session_id,
        project_slug=slug,
        project_path=str(project_path),
        resolved=resolved,
        transcript_path=transcript_path,
        has_state=state is not None,
        started_at=_coerce_started_at(state),
        pid=pid if isinstance(pid, int) else None,
        title=title,
        last_activity=last_activity,
        event_count=event_count,
    )


def _authoritative_cwd(state: dict | None, transcript_path: Path | None) -> str | None:
    """Return the authoritative project cwd from state, else the transcript."""
    if state is not None:
        cwd = state.get("cwd")
        if isinstance(cwd, str) and cwd:
            return cwd
    if transcript_path is not None:
        return read_transcript_cwd(transcript_path)
    return None


def _coerce_started_at(state: dict | None) -> datetime | None:
    """Extract and parse ``started_at`` from a state dict, tolerating absence."""
    if state is None:
        return None
    raw = state.get("started_at")
    return _parse_timestamp(raw) if isinstance(raw, str) else None


def _discover_slug(slug_dir: Path) -> list[SessionRecord]:
    """Discover every session for a single slug directory."""
    slug: str = slug_dir.name
    transcripts: dict[str, Path] = _list_transcripts(slug_dir)
    project_root: Path = _resolve_project_root(slug, transcripts)
    state_by_sid: dict[str, dict] = _collect_state(project_root, transcripts)
    records: list[SessionRecord] = []
    for sid in transcripts.keys() | state_by_sid.keys():
        records.append(
            _build_record(sid, slug, transcripts.get(sid), state_by_sid.get(sid))
        )
    return records


def _resolve_project_root(slug: str, transcripts: dict[str, Path]) -> Path:
    """Resolve the project root via a transcript ``cwd``, else slug-reverse.

    A transcript ``cwd`` is authoritative because the slug transform is lossy.
    When no transcript carries a ``cwd`` the best-effort slug reverse is used.
    """
    for transcript_path in transcripts.values():
        cwd = read_transcript_cwd(transcript_path)
        if cwd is not None:
            return Path(cwd)
    return slug_to_path(slug)


def _collect_state(project_root: Path, transcripts: dict[str, Path]) -> dict[str, dict]:
    """Collect ``session-started.json`` payloads keyed by session id.

    The candidate ids are the union of the project's state-directory session
    ids and the transcript session ids; only payloads that read successfully
    are kept (so transcript-only sessions remain stateless).
    """
    state_ids: set[str] = _list_state_session_ids(project_root) | set(transcripts)
    result: dict[str, dict] = {}
    for sid in state_ids:
        state = read_session_started(project_root, sid)
        if state is not None:
            result[sid] = state
    return result


def discover_sessions(projects_root: Path) -> list[SessionRecord]:
    """Enumerate all sessions under ``projects_root`` (transcripts ∪ state).

    Each immediate child directory of ``projects_root`` is a project slug.
    For every slug the transcript files and the resolved project's state
    sessions are unioned, tolerating either source being absent (D7).
    """
    records: list[SessionRecord] = []
    try:
        slug_dirs = [child for child in projects_root.iterdir() if child.is_dir()]
    except OSError as exc:
        logger.debug("cannot enumerate projects root %s: %s", projects_root, exc)
        return records
    for slug_dir in slug_dirs:
        records.extend(_discover_slug(slug_dir))
    return records
