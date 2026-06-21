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

Performance notes
-----------------
* ``scan_transcript`` performs a single pass over a JSONL file, aggregating
  title, last-activity timestamp, event count, and first ``cwd`` (PERF1).
* ``find_session`` short-circuits enumeration as soon as the target session
  id is matched, avoiding unnecessary transcript reads (PERF2).
"""

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from app.services.resolver import resolve_project, slug_to_path

if TYPE_CHECKING:
    from app.core.config import Settings

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


@dataclass(frozen=True)
class ScanResult:
    """Aggregated result of a single-pass transcript scan (PERF1).

    All four fields are extracted in one pass over ``_iter_json_lines``,
    replacing the previous triple-open pattern of calling
    ``read_last_ai_title``, ``compute_last_activity``, and ``count_events``
    separately plus ``read_transcript_cwd``.
    """

    title: str | None
    last_activity: datetime | None
    event_count: int
    cwd: str | None


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


def scan_transcript(transcript_path: Path) -> ScanResult:
    """Aggregate title, last-activity, event count, and first cwd in one pass (PERF1).

    Replaces the former pattern of calling ``read_last_ai_title``,
    ``compute_last_activity``, ``count_events``, and ``read_transcript_cwd``
    separately (which opened the file three to four times).  The individual
    helpers remain available for callers that need them in isolation.

    A missing or unreadable file yields a zero-value ``ScanResult`` with all
    optional fields set to ``None``.
    """
    title: str | None = None
    latest: datetime | None = None
    cwd: str | None = None
    event_count: int = 0

    for obj in _iter_json_lines(transcript_path):
        if not isinstance(obj, dict):
            continue
        event_count += 1

        # First cwd seen wins
        if cwd is None:
            raw_cwd = obj.get("cwd")
            if isinstance(raw_cwd, str) and raw_cwd:
                cwd = raw_cwd

        # Last ai-title wins
        if obj.get("type") == "ai-title":
            value = obj.get("aiTitle")
            if isinstance(value, str):
                title = value

        # Max timestamp
        if obj.get("type") in _TIMESTAMP_TYPES:
            raw_ts = obj.get("timestamp")
            if isinstance(raw_ts, str):
                parsed = _parse_timestamp(raw_ts)
                if parsed is not None and (latest is None or parsed > latest):
                    latest = parsed

    # Fallback to mtime when no timestamped line was found (mirrors compute_last_activity)
    if latest is None and event_count > 0:
        latest = _mtime(transcript_path)

    return ScanResult(title=title, last_activity=latest, event_count=event_count, cwd=cwd)


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
    else from the transcript itself (via ``scan_transcript``); both recover the
    true project path even when the slug is lossy.  A single ``scan_transcript``
    call replaces the former triple-open pattern (PERF1).
    """
    if transcript_path is not None:
        scan = scan_transcript(transcript_path)
        transcript_cwd: str | None = scan.cwd
        title: str | None = scan.title
        last_activity: datetime | None = scan.last_activity
        event_count: int | None = scan.event_count
    else:
        transcript_cwd = None
        title = None
        last_activity = None
        event_count = None

    cwd = _authoritative_cwd_from(state, transcript_cwd)
    project_path, resolved = resolve_project(slug, cwd)
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


def _authoritative_cwd_from(state: dict | None, transcript_cwd: str | None) -> str | None:
    """Return the authoritative project cwd from state, else the pre-scanned transcript cwd.

    Replaces ``_authoritative_cwd`` to accept the already-extracted ``transcript_cwd``
    from ``scan_transcript``, avoiding a redundant file open (PERF1).
    """
    if state is not None:
        cwd = state.get("cwd")
        if isinstance(cwd, str) and cwd:
            return cwd
    return transcript_cwd


def _coerce_started_at(state: dict | None) -> datetime | None:
    """Extract and parse ``started_at`` from a state dict, tolerating absence."""
    if state is None:
        return None
    raw = state.get("started_at")
    return _parse_timestamp(raw) if isinstance(raw, str) else None


def _discover_slug(slug_dir: Path) -> list[SessionRecord]:
    """Discover every session for a single slug directory.

    Scans every transcript once (PERF1) and caches the results so that
    ``_resolve_project_root`` can reuse the already-extracted ``cwd`` without
    opening any file a second time.
    """
    slug: str = slug_dir.name
    transcripts: dict[str, Path] = _list_transcripts(slug_dir)
    scans: dict[str, ScanResult] = {
        sid: scan_transcript(path) for sid, path in transcripts.items()
    }
    project_root: Path = _resolve_project_root_from_scans(slug, scans)
    state_by_sid: dict[str, dict] = _collect_state(project_root, transcripts)
    records: list[SessionRecord] = []
    for sid in transcripts.keys() | state_by_sid.keys():
        records.append(
            _build_record_from_scan(
                sid,
                slug,
                transcripts.get(sid),
                scans.get(sid),
                state_by_sid.get(sid),
            )
        )
    return records


def _resolve_project_root_from_scans(
    slug: str, scans: dict[str, "ScanResult"]
) -> Path:
    """Resolve the project root from pre-scanned transcript cwds, else slug-reverse.

    Reuses ``ScanResult.cwd`` from the single-pass scan, so no file is opened
    again for project-root resolution (PERF1).
    """
    for scan in scans.values():
        if scan.cwd is not None:
            return Path(scan.cwd)
    return slug_to_path(slug)


def _build_record_from_scan(
    session_id: str,
    slug: str,
    transcript_path: Path | None,
    scan: "ScanResult | None",
    state: dict | None,
) -> SessionRecord:
    """Assemble a ``SessionRecord`` from a pre-computed ``ScanResult`` (PERF1).

    Called by ``_discover_slug`` which has already scanned every transcript
    once.  For sessions without a transcript (state-only, D7), ``scan`` is
    ``None`` and all transcript-derived fields default to ``None``.
    """
    if scan is not None:
        transcript_cwd: str | None = scan.cwd
        title: str | None = scan.title
        last_activity: datetime | None = scan.last_activity
        event_count: int | None = scan.event_count
    else:
        transcript_cwd = None
        title = None
        last_activity = None
        event_count = None

    cwd = _authoritative_cwd_from(state, transcript_cwd)
    project_path, resolved = resolve_project(slug, cwd)
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


def _classify_for_record(record: SessionRecord, settings: "Settings") -> str:
    """Classify the session state for a record using the given settings (CLEAN1).

    Centralises the ``classify_state`` call shared by ``_to_summary`` and
    ``_to_detail`` so the thresholds are read from ``settings`` exactly once.
    """
    return classify_state(
        record.last_activity,
        datetime.now(timezone.utc),
        settings.session_active_threshold_s,
        settings.session_recent_threshold_s,
    )


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


def _find_in_slug(slug_dir: Path, session_id: str) -> SessionRecord | None:
    """Search a single slug directory for ``session_id``, scanning lazily (PERF2).

    Strategy:
    1. Check transcript filenames (free — no file reads).  If the session has a
       transcript, scan transcripts (for cwd + fields) and return early.
    2. Otherwise extract the project root from any transcript cwd (scanning at
       most all transcripts once for root resolution), then check state dirs.
    3. If ``session_id`` is not found in either source, return ``None`` without
       touching any other slug directory.

    Path-traversal safety: ``session_id`` is compared only against ids derived
    from filesystem enumeration, never used to build a file path directly.
    """
    slug: str = slug_dir.name
    transcripts: dict[str, Path] = _list_transcripts(slug_dir)

    # Fast path: target is a known transcript — scan transcripts to get cwd and fields.
    if session_id in transcripts:
        project_root, target_scan, _ = _scan_transcripts_for_root(slug, transcripts, session_id)
        state_by_sid = _collect_state(project_root, transcripts)
        return _build_record_from_scan(
            session_id, slug, transcripts[session_id], target_scan, state_by_sid.get(session_id)
        )

    # Slow path: target may be a state-only session.  Resolve the real project
    # root from transcript cwds (needed to find the state directory), then check.
    project_root, _, _ = _scan_transcripts_for_root(slug, transcripts, None)
    state_sids: set[str] = _list_state_session_ids(project_root)
    if session_id not in state_sids:
        return None

    state_by_sid = _collect_state(project_root, transcripts)
    return _build_record_from_scan(
        session_id, slug, None, None, state_by_sid.get(session_id)
    )


def _scan_transcripts_for_root(
    slug: str,
    transcripts: dict[str, Path],
    target_sid: str | None,
) -> tuple[Path, "ScanResult | None", dict[str, ScanResult]]:
    """Scan transcripts to extract the project root and optionally a target scan.

    Returns ``(project_root, target_scan_or_None, all_scans)``.  Stops scanning
    once both the project root cwd and the target scan (if requested) are found.
    """
    project_root: Path | None = None
    target_scan: ScanResult | None = None
    all_scans: dict[str, ScanResult] = {}

    for sid, path in transcripts.items():
        scan = scan_transcript(path)
        all_scans[sid] = scan
        if sid == target_sid:
            target_scan = scan
        if project_root is None and scan.cwd is not None:
            project_root = Path(scan.cwd)

    if project_root is None:
        project_root = slug_to_path(slug)

    return project_root, target_scan, all_scans


def find_session(projects_root: Path, session_id: str) -> SessionRecord | None:
    """Return the first record matching ``session_id``, or ``None`` (PERF2).

    Iterates slug directories in sorted order, stopping as soon as the target
    session id is found within a slug.  Within each slug, only the matching
    session's transcript is fully processed; other transcripts in the same slug
    are only scanned for project-root resolution, not for all their fields.

    Path-traversal safety is preserved: ``session_id`` is only compared
    against ids produced by the enumeration (never used to build a file path).
    """
    try:
        slug_dirs = sorted(
            (child for child in projects_root.iterdir() if child.is_dir()),
            key=lambda p: p.name,
        )
    except OSError as exc:
        logger.debug("cannot enumerate projects root %s: %s", projects_root, exc)
        return None
    for slug_dir in slug_dirs:
        record = _find_in_slug(slug_dir, session_id)
        if record is not None:
            return record
    return None
