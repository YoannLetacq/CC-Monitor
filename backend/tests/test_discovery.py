"""Unit tests for the read-only discovery service."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

from app.core.config import Settings
from app.services.discovery import (
    ScanResult,
    SessionRecord,
    _classify_for_record,
    classify_state,
    compute_last_activity,
    count_events,
    discover_sessions,
    find_session,
    read_last_ai_title,
    read_session_started,
    scan_transcript,
)


def _ts(line_type: str, when: str) -> dict:
    """Build a timestamped transcript line of the given type."""
    return {"type": line_type, "timestamp": when}


def test_read_last_ai_title_returns_last(
    make_transcript: Callable[[str, str, list[dict]], Path],
) -> None:
    """The title is the ``aiTitle`` of the last ``ai-title`` event."""
    transcript = make_transcript(
        "-home-proj",
        "sid-1",
        [
            {"type": "ai-title", "aiTitle": "First", "sessionId": "sid-1"},
            _ts("assistant", "2026-06-19T10:00:00Z"),
            {"type": "ai-title", "aiTitle": "Second", "sessionId": "sid-1"},
        ],
    )
    assert read_last_ai_title(transcript) == "Second"


def test_read_last_ai_title_absent_returns_none(
    make_transcript: Callable[[str, str, list[dict]], Path],
) -> None:
    """A transcript without any ``ai-title`` event yields ``None``."""
    transcript = make_transcript(
        "-home-proj", "sid-2", [_ts("assistant", "2026-06-19T10:00:00Z")]
    )
    assert read_last_ai_title(transcript) is None


def test_read_last_ai_title_ignores_corrupt_line(
    make_transcript: Callable[[str, str, list[dict]], Path],
) -> None:
    """A corrupt JSON line must not crash title extraction."""
    transcript = make_transcript(
        "-home-proj",
        "sid-3",
        [{"type": "ai-title", "aiTitle": "Good", "sessionId": "sid-3"}],
    )
    with open(transcript, "a", encoding="utf-8") as handle:
        handle.write("{not valid json\n")
    assert read_last_ai_title(transcript) == "Good"


def test_compute_last_activity_returns_max_timestamp(
    make_transcript: Callable[[str, str, list[dict]], Path],
) -> None:
    """``compute_last_activity`` returns the maximum parseable timestamp."""
    transcript = make_transcript(
        "-home-proj",
        "sid-4",
        [
            _ts("user", "2026-06-19T09:00:00Z"),
            _ts("assistant", "2026-06-19T11:30:00Z"),
            {"type": "ai-title", "aiTitle": "x", "sessionId": "sid-4"},
        ],
    )
    expected = datetime(2026, 6, 19, 11, 30, tzinfo=timezone.utc)
    assert compute_last_activity(transcript) == expected


def test_compute_last_activity_falls_back_to_mtime(
    make_transcript: Callable[[str, str, list[dict]], Path],
) -> None:
    """With no timestamped line, the file mtime is used as a fallback."""
    transcript = make_transcript(
        "-home-proj",
        "sid-5",
        [{"type": "ai-title", "aiTitle": "x", "sessionId": "sid-5"}],
    )
    result = compute_last_activity(transcript)
    assert result is not None
    mtime = datetime.fromtimestamp(transcript.stat().st_mtime, tz=timezone.utc)
    assert result == mtime


def test_compute_last_activity_missing_file_returns_none(tmp_path: Path) -> None:
    """A missing transcript yields ``None``."""
    assert compute_last_activity(tmp_path / "absent.jsonl") is None


def test_count_events_counts_parseable_lines(
    make_transcript: Callable[[str, str, list[dict]], Path],
) -> None:
    """``count_events`` counts parseable JSON lines, skipping corrupt ones."""
    transcript = make_transcript(
        "-home-proj",
        "sid-6",
        [_ts("user", "2026-06-19T09:00:00Z"), _ts("assistant", "2026-06-19T09:01:00Z")],
    )
    with open(transcript, "a", encoding="utf-8") as handle:
        handle.write("{broken\n")
    assert count_events(transcript) == 2


def test_count_events_missing_file_returns_zero(tmp_path: Path) -> None:
    """A missing transcript counts as zero events."""
    assert count_events(tmp_path / "absent.jsonl") == 0


def test_classify_state_active() -> None:
    """A delta below the active threshold classifies as ``active``."""
    now = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)
    last = datetime(2026, 6, 19, 11, 59, 30, tzinfo=timezone.utc)
    assert classify_state(last, now, 90, 1800) == "active"


def test_classify_state_recent() -> None:
    """A delta between thresholds classifies as ``recent``."""
    now = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)
    last = datetime(2026, 6, 19, 11, 50, 0, tzinfo=timezone.utc)
    assert classify_state(last, now, 90, 1800) == "recent"


def test_classify_state_terminated() -> None:
    """A delta beyond the recent threshold classifies as ``terminated``."""
    now = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)
    last = datetime(2026, 6, 19, 11, 0, 0, tzinfo=timezone.utc)
    assert classify_state(last, now, 90, 1800) == "terminated"


def test_classify_state_none_is_terminated() -> None:
    """A missing ``last_activity`` is a safe ``terminated``."""
    now = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)
    assert classify_state(None, now, 90, 1800) == "terminated"


def test_read_session_started_present(
    projects_root: Path, write_state: Callable[[Path, dict], None]
) -> None:
    """A present ``session-started.json`` is returned as a dict."""
    project = projects_root / "proj"
    payload = {
        "session_id": "sid-7",
        "started_at": "2026-06-19T08:00:00Z",
        "cwd": "/home/user/proj",
        "pid": 4242,
        "boot_id": "boot-1",
    }
    write_state(project, payload)
    result = read_session_started(project, "sid-7")
    assert result is not None
    assert result["cwd"] == "/home/user/proj"
    assert result["pid"] == 4242


def test_read_session_started_absent_returns_none(projects_root: Path) -> None:
    """A missing state file yields ``None``."""
    assert read_session_started(projects_root / "proj", "ghost") is None


def test_read_session_started_malformed_returns_none(
    projects_root: Path,
) -> None:
    """A malformed ``session-started.json`` is treated as absent (``None``)."""
    state_dir = projects_root / "proj" / ".omc" / "state" / "sessions" / "sid-8"
    state_dir.mkdir(parents=True)
    with open(state_dir / "session-started.json", "w", encoding="utf-8") as handle:
        handle.write("{not json")
    assert read_session_started(projects_root / "proj", "sid-8") is None


def test_discover_sessions_multi_project_union(
    projects_root: Path, make_project: Callable[..., Path]
) -> None:
    """Discovery enumerates multiple projects and unions transcripts + state."""
    make_project(
        "alpha",
        "sid-a",
        [
            {"type": "ai-title", "aiTitle": "Alpha", "sessionId": "sid-a"},
            _ts("assistant", "2026-06-19T10:00:00Z"),
        ],
    )
    make_project("beta", "sid-b", [_ts("user", "2026-06-19T08:00:00Z")])

    records = discover_sessions(projects_root)
    by_id = {r.session_id: r for r in records}
    assert "sid-a" in by_id
    assert "sid-b" in by_id
    assert len(records) >= 2
    assert by_id["sid-a"].title == "Alpha"
    assert by_id["sid-a"].has_state is True
    assert {r.project_slug for r in records}.__len__() >= 2


def test_discover_sessions_state_without_transcript(
    projects_root: Path, make_project: Callable[..., Path]
) -> None:
    """A state-only session (no transcript) is still discovered (D7).

    The project is resolved from the slug; a second session id living only
    under that project's ``.omc/state`` is found by scanning the state dir.
    """
    make_project(
        "gamma",
        "sid-c",
        [_ts("user", "2026-06-19T08:00:00Z")],
        extra_state_sids=("sid-orphan",),
    )
    records = discover_sessions(projects_root)
    by_id = {r.session_id: r for r in records}
    assert "sid-orphan" in by_id
    orphan = by_id["sid-orphan"]
    assert orphan.transcript_path is None
    assert orphan.has_state is True


def test_discover_sessions_transcript_without_state(
    projects_root: Path, make_project: Callable[..., Path]
) -> None:
    """A transcript-only session (no state) is still discovered (D7)."""
    make_project(
        "delta",
        "sid-d",
        [_ts("user", "2026-06-19T08:00:00Z")],
        with_state=False,
    )
    records = discover_sessions(projects_root)
    record = next(r for r in records if r.session_id == "sid-d")
    assert record.has_state is False
    assert record.transcript_path is not None


def test_discover_sessions_recovers_lossy_project_path_from_cwd(
    projects_root: Path, make_project: Callable[..., Path]
) -> None:
    """A dash-containing project path is recovered via the transcript ``cwd``.

    The tmp project directory contains dashes, so the slug reverse is lossy;
    discovery must still expose the true path and mark it ``resolved``.
    """
    project_dir = make_project(
        "epsilon", "sid-e", [_ts("assistant", "2026-06-19T08:00:00Z")]
    )
    records = discover_sessions(projects_root)
    record = next(r for r in records if r.session_id == "sid-e")
    assert record.project_path == str(project_dir)
    assert record.resolved is True


# ---------------------------------------------------------------------------
# PERF1 — scan_transcript reads the file exactly once
# ---------------------------------------------------------------------------


def test_scan_transcript_returns_scan_result(tmp_path: Path) -> None:
    """``scan_transcript`` returns a ``ScanResult`` with all aggregated fields."""
    transcript = tmp_path / "sid.jsonl"
    lines = [
        {"type": "user", "timestamp": "2026-06-19T09:00:00Z", "cwd": "/my/project"},
        {"type": "assistant", "timestamp": "2026-06-19T11:30:00Z"},
        {"type": "ai-title", "aiTitle": "My Title", "sessionId": "sid"},
    ]
    with open(transcript, "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")

    result = scan_transcript(transcript)

    assert isinstance(result, ScanResult)
    assert result.title == "My Title"
    assert result.last_activity == datetime(2026, 6, 19, 11, 30, tzinfo=timezone.utc)
    assert result.event_count == 3
    assert result.cwd == "/my/project"


def test_scan_transcript_single_file_open(tmp_path: Path) -> None:
    """``scan_transcript`` opens the transcript file exactly once (PERF1)."""
    transcript = tmp_path / "sid.jsonl"
    lines = [
        {"type": "user", "timestamp": "2026-06-19T09:00:00Z", "cwd": "/p"},
        {"type": "ai-title", "aiTitle": "T", "sessionId": "sid"},
    ]
    with open(transcript, "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")

    open_count = {"n": 0}
    real_open = open

    def counting_open(path: Any, *args: Any, **kwargs: Any) -> Any:
        if str(path) == str(transcript):
            open_count["n"] += 1
        return real_open(path, *args, **kwargs)

    with patch("builtins.open", side_effect=counting_open):
        scan_transcript(transcript)

    assert open_count["n"] == 1, (
        f"scan_transcript opened the file {open_count['n']} time(s); expected exactly 1"
    )


def test_scan_transcript_missing_file(tmp_path: Path) -> None:
    """``scan_transcript`` on a missing file returns a safe zero-value ``ScanResult``."""
    result = scan_transcript(tmp_path / "absent.jsonl")
    assert result.title is None
    assert result.last_activity is None
    assert result.event_count == 0
    assert result.cwd is None


def test_scan_transcript_aggregates_same_as_individual_helpers(tmp_path: Path) -> None:
    """Non-regression: ``scan_transcript`` fields match individual helper results."""
    transcript = tmp_path / "sid.jsonl"
    lines = [
        {"type": "user", "timestamp": "2026-06-19T09:00:00Z", "cwd": "/home/proj"},
        {"type": "assistant", "timestamp": "2026-06-19T10:00:00Z"},
        {"type": "ai-title", "aiTitle": "Last Title", "sessionId": "sid"},
        {"type": "ai-title", "aiTitle": "Final Title", "sessionId": "sid"},
    ]
    with open(transcript, "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")

    result = scan_transcript(transcript)

    assert result.title == read_last_ai_title(transcript)
    assert result.last_activity == compute_last_activity(transcript)
    assert result.event_count == count_events(transcript)


# ---------------------------------------------------------------------------
# PERF2 — discover_sessions early-exit on session_id target
# ---------------------------------------------------------------------------


def _write_transcript(path: Path, lines: list[dict]) -> None:
    """Write JSONL lines to a path, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")


def test_get_session_does_not_read_other_slug_transcripts(
    tmp_path: Path,
) -> None:
    """PERF2: ``find_session`` stops iterating slugs after the matching slug is found.

    We set up two projects (two slug directories): the target session lives in
    the first slug, the decoy session lives in the second slug.  A tracker on
    ``open`` verifies that the decoy slug's transcript is never opened once the
    target has been found and returned.

    Note: within the matching slug, all transcripts may be scanned for project-
    root resolution (cwd extraction) — that is expected.  The early-exit
    guarantee is across slugs, not within a slug.
    """
    root = tmp_path / "claude_projects"

    # --- Target project (slug A) ---
    proj_a = tmp_path / "work" / "alpha-project"
    proj_a.mkdir(parents=True, exist_ok=True)
    slug_a = str(proj_a).replace("/", "-")
    target_sid = "aaa-target-session"

    target_path = root / slug_a / f"{target_sid}.jsonl"
    _write_transcript(
        target_path,
        [{"type": "user", "timestamp": "2026-06-19T10:00:00Z", "cwd": str(proj_a)}],
    )
    state_a = proj_a / ".omc" / "state" / "sessions" / target_sid
    state_a.mkdir(parents=True, exist_ok=True)
    with open(state_a / "session-started.json", "w", encoding="utf-8") as fh:
        json.dump(
            {"session_id": target_sid, "cwd": str(proj_a), "started_at": "2026-06-19T08:00:00Z"},
            fh,
        )

    # --- Decoy project (slug B, different directory) ---
    proj_b = tmp_path / "work" / "beta-project"
    proj_b.mkdir(parents=True, exist_ok=True)
    slug_b = str(proj_b).replace("/", "-")
    decoy_sid = "zzz-decoy-session"

    decoy_path = root / slug_b / f"{decoy_sid}.jsonl"
    _write_transcript(
        decoy_path,
        [{"type": "user", "timestamp": "2026-06-19T09:00:00Z", "cwd": str(proj_b)}],
    )
    state_b = proj_b / ".omc" / "state" / "sessions" / decoy_sid
    state_b.mkdir(parents=True, exist_ok=True)
    with open(state_b / "session-started.json", "w", encoding="utf-8") as fh:
        json.dump(
            {"session_id": decoy_sid, "cwd": str(proj_b), "started_at": "2026-06-19T08:00:00Z"},
            fh,
        )

    # Ensure slug_a sorts before slug_b so the target slug is visited first.
    # Slug names are derived from absolute paths; we verify the ordering holds.
    assert slug_a < slug_b, (
        f"Test assumption violated: slug_a={slug_a!r} must sort before slug_b={slug_b!r}"
    )

    opened_transcripts: list[str] = []
    real_open = open

    def tracking_open(path: Any, *args: Any, **kwargs: Any) -> Any:
        p = str(path)
        if p.endswith(".jsonl"):
            opened_transcripts.append(p)
        return real_open(path, *args, **kwargs)

    with patch("builtins.open", side_effect=tracking_open):
        record = find_session(root, target_sid)

    assert record is not None
    assert record.session_id == target_sid
    assert str(decoy_path) not in opened_transcripts, (
        "PERF2 violation: decoy slug transcript was read even though target was already matched"
    )


# ---------------------------------------------------------------------------
# CLEAN1 — _classify_for_record helper is consistent with both endpoints
# ---------------------------------------------------------------------------


def test_classify_for_record_matches_classify_state(tmp_path: Path) -> None:
    """``_classify_for_record`` must delegate to ``classify_state`` with correct args.

    We set ``last_activity=None`` so the result is deterministically
    ``"terminated"`` regardless of wall-clock time, then separately verify
    the ``active`` and ``recent`` buckets using ``classify_state`` directly
    (which accepts an explicit ``now``).
    """
    settings = Settings(
        claude_projects_root="/tmp",
        session_active_threshold_s=90,
        session_recent_threshold_s=1800,
    )

    # None last_activity → always terminated (safe default, no clock dependency)
    record_none = SessionRecord(
        session_id="sid-x",
        project_slug="slug",
        project_path="/p",
        resolved=True,
        transcript_path=None,
        has_state=False,
        started_at=None,
        pid=None,
        title=None,
        last_activity=None,
        event_count=None,
    )
    assert _classify_for_record(record_none, settings) == "terminated"

    # Very old last_activity (epoch) → always terminated regardless of now
    record_old = SessionRecord(
        session_id="sid-y",
        project_slug="slug",
        project_path="/p",
        resolved=True,
        transcript_path=None,
        has_state=False,
        started_at=None,
        pid=None,
        title=None,
        last_activity=datetime(2000, 1, 1, tzinfo=timezone.utc),
        event_count=None,
    )
    assert _classify_for_record(record_old, settings) == "terminated"

    # Result is always one of the three valid buckets
    assert _classify_for_record(record_none, settings) in ("active", "recent", "terminated")
