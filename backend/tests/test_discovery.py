"""Unit tests for the read-only discovery service."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.services.discovery import (
    classify_state,
    compute_last_activity,
    count_events,
    discover_sessions,
    read_last_ai_title,
    read_session_started,
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
