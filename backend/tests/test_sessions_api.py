"""API integration tests for the sessions endpoints (W2).

All tests use an anonymized temporary fixture tree — the real ~/.claude is
never read. ``get_settings`` is overridden per-test via FastAPI dependency
injection to point ``claude_projects_root`` at the tmp fixture directory.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_jsonl(path: Path, lines: list[dict]) -> None:
    """Write one JSON object per line to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line))
            handle.write("\n")


def _write_session_started(project_root: Path, payload: dict) -> None:
    """Write a ``session-started.json`` for the given project root."""
    sid = payload["session_id"]
    state_dir = project_root / ".omc" / "state" / "sessions" / sid
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / "session-started.json", "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def _make_client(projects_root: Path) -> TestClient:
    """Return a TestClient with ``claude_projects_root`` overridden to ``projects_root``."""
    overridden = Settings(
        claude_projects_root=str(projects_root),
        session_active_threshold_s=90,
        session_recent_threshold_s=1800,
    )
    app.dependency_overrides[get_settings] = lambda: overridden
    client = TestClient(app)
    return client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fixture_projects_root(tmp_path: Path) -> Path:
    """Return the anonymized projects root directory."""
    root = tmp_path / "claude_projects"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture()
def two_project_setup(
    tmp_path: Path, fixture_projects_root: Path
) -> dict:
    """Build two projects with known sessions and return metadata for assertions.

    Project A: session with title, recent activity.
    Project B: session without title, older activity.
    """
    work = tmp_path / "work"

    # --- Project A: session with title, known recent timestamp ---
    proj_a = work / "project-alpha"
    proj_a.mkdir(parents=True, exist_ok=True)
    slug_a = str(proj_a).replace("/", "-")
    sid_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    lines_a = [
        {
            "type": "user",
            "timestamp": "2026-06-19T10:00:00Z",
            "cwd": str(proj_a),
            "message": {"role": "user", "content": "hello"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-06-19T10:01:00Z",
            "cwd": str(proj_a),
            "message": {"role": "assistant", "content": "hi"},
        },
        {"type": "ai-title", "aiTitle": "Alpha session title", "sessionId": sid_a},
    ]
    _write_jsonl(fixture_projects_root / slug_a / f"{sid_a}.jsonl", lines_a)
    _write_session_started(
        proj_a,
        {
            "session_id": sid_a,
            "started_at": "2026-06-19T09:00:00Z",
            "cwd": str(proj_a),
            "pid": 1001,
            "boot_id": "boot-a",
        },
    )

    # --- Project B: session without title, older timestamp ---
    proj_b = work / "project-beta"
    proj_b.mkdir(parents=True, exist_ok=True)
    slug_b = str(proj_b).replace("/", "-")
    sid_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    lines_b = [
        {
            "type": "user",
            "timestamp": "2026-06-19T08:00:00Z",
            "cwd": str(proj_b),
            "message": {"role": "user", "content": "older"},
        },
    ]
    _write_jsonl(fixture_projects_root / slug_b / f"{sid_b}.jsonl", lines_b)
    _write_session_started(
        proj_b,
        {
            "session_id": sid_b,
            "started_at": "2026-06-19T07:30:00Z",
            "cwd": str(proj_b),
            "pid": 1002,
            "boot_id": "boot-b",
        },
    )

    return {
        "sid_a": sid_a,
        "sid_b": sid_b,
        "projects_root": fixture_projects_root,
    }


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> None:
    """Remove all dependency overrides after each test."""
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Test 1: GET /api/sessions returns 200, non-empty list, camelCase, sorted
# ---------------------------------------------------------------------------

def test_list_sessions_returns_200_sorted_camelcase(
    two_project_setup: dict,
) -> None:
    """GET /api/sessions must return 200, non-empty list, camelCase, sorted by lastActivity desc."""
    client = _make_client(two_project_setup["projects_root"])
    response = client.get("/api/sessions")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 2

    # All items must have camelCase fields
    item = data[0]
    assert "sessionId" in item
    assert "projectPath" in item
    assert "projectSlug" in item
    assert "resolved" in item
    assert "state" in item
    assert "lastActivity" in item or item.get("lastActivity") is None
    assert "startedAt" in item or item.get("startedAt") is None

    # Must be sorted: more recent lastActivity first (null last)
    activities = [
        datetime.fromisoformat(item["lastActivity"])
        for item in data
        if item.get("lastActivity") is not None
    ]
    assert activities == sorted(activities, reverse=True)


# ---------------------------------------------------------------------------
# Test 2: title present on one item, null on another; state correct
# ---------------------------------------------------------------------------

def test_list_sessions_title_and_state(
    two_project_setup: dict,
) -> None:
    """One session has a title, another title=null; state is one of the valid values."""
    client = _make_client(two_project_setup["projects_root"])
    response = client.get("/api/sessions")

    assert response.status_code == 200
    data = response.json()
    sid_a = two_project_setup["sid_a"]
    sid_b = two_project_setup["sid_b"]

    by_id = {item["sessionId"]: item for item in data}
    assert sid_a in by_id
    assert sid_b in by_id

    assert by_id[sid_a]["title"] == "Alpha session title"
    assert by_id[sid_b]["title"] is None

    valid_states = {"active", "recent", "terminated"}
    for item in data:
        assert item["state"] in valid_states


# ---------------------------------------------------------------------------
# Test 3: GET /api/sessions/{sid} known → 200 + SessionDetail
# ---------------------------------------------------------------------------

def test_get_session_detail_known(two_project_setup: dict) -> None:
    """GET /api/sessions/{known_sid} must return 200 with full SessionDetail fields."""
    client = _make_client(two_project_setup["projects_root"])
    sid = two_project_setup["sid_a"]
    response = client.get(f"/api/sessions/{sid}")

    assert response.status_code == 200
    data = response.json()

    # Summary fields
    assert data["sessionId"] == sid
    assert "projectPath" in data
    assert "projectSlug" in data
    assert "resolved" in data
    assert "state" in data

    # Detail-only fields
    assert "transcriptPath" in data
    assert data["transcriptPath"] is not None
    assert "hasState" in data
    assert data["hasState"] is True
    assert "eventCount" in data
    assert isinstance(data["eventCount"], int)
    assert data["eventCount"] > 0


# ---------------------------------------------------------------------------
# Test 4: GET /api/sessions/{sid} unknown → 404
# ---------------------------------------------------------------------------

def test_get_session_detail_unknown_returns_404(
    fixture_projects_root: Path,
) -> None:
    """GET /api/sessions/{unknown_sid} must return 404."""
    client = _make_client(fixture_projects_root)
    response = client.get("/api/sessions/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Test 5: Path-traversal attempts → 404 or 422, never a file read
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "malicious_sid",
    [
        "../x",
        "../../etc/passwd",
        "-home-yoann-.claude-projects-other-session",
    ],
)
def test_path_traversal_blocked(
    malicious_sid: str, fixture_projects_root: Path
) -> None:
    """Malicious session_id values must return 404 (not a file read outside enumeration)."""
    client = _make_client(fixture_projects_root)
    # Use the raw string directly — the test client encodes slashes
    response = client.get(f"/api/sessions/{malicious_sid}")
    # 404 (not in enumeration) or 422 (validation error) — never 200 or 500
    assert response.status_code in (404, 422)


def test_path_traversal_encoded_blocked(fixture_projects_root: Path) -> None:
    """URL-encoded path traversal must return 404 or 422."""
    client = _make_client(fixture_projects_root)
    # httpx will decode %2F in path segments; we test via explicit path
    response = client.get("/api/sessions/..%2F..%2Fetc%2Fpasswd")
    assert response.status_code in (404, 422)


# ---------------------------------------------------------------------------
# Test 6: State-without-transcript → transcriptPath=null, eventCount=null tolerated
# ---------------------------------------------------------------------------

def test_state_without_transcript_returns_null_fields(
    tmp_path: Path, fixture_projects_root: Path
) -> None:
    """A state-only session (no transcript) must return transcriptPath=null, eventCount=null.

    We use a project that has one anchoring transcript (so ``cwd`` is known
    and the project root is resolved correctly), plus a second session id that
    lives only in the state directory — matching the D7 asymmetry pattern
    verified by W1 tests.
    """
    work = tmp_path / "work"
    proj = work / "stateonly"
    proj.mkdir(parents=True, exist_ok=True)
    slug = str(proj).replace("/", "-")

    # Anchor transcript: gives discovery the cwd so the project root is known
    anchor_sid = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    anchor_lines = [
        {
            "type": "user",
            "timestamp": "2026-06-19T07:00:00Z",
            "cwd": str(proj),
            "message": {"role": "user", "content": "anchor"},
        },
    ]
    _write_jsonl(fixture_projects_root / slug / f"{anchor_sid}.jsonl", anchor_lines)
    _write_session_started(
        proj,
        {
            "session_id": anchor_sid,
            "started_at": "2026-06-19T07:00:00Z",
            "cwd": str(proj),
            "pid": 3001,
            "boot_id": "boot-d",
        },
    )

    # State-only session: no transcript, only state dir
    state_sid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    _write_session_started(
        proj,
        {
            "session_id": state_sid,
            "started_at": "2026-06-19T08:00:00Z",
            "cwd": str(proj),
            "pid": 2001,
            "boot_id": "boot-c",
        },
    )

    client = _make_client(fixture_projects_root)
    response = client.get(f"/api/sessions/{state_sid}")

    assert response.status_code == 200
    data = response.json()
    assert data["sessionId"] == state_sid
    assert data["transcriptPath"] is None
    assert data["eventCount"] is None
    assert data["hasState"] is True


# ---------------------------------------------------------------------------
# Test 7: sort=oldest reverses the order
# ---------------------------------------------------------------------------

def test_list_sessions_sort_oldest(two_project_setup: dict) -> None:
    """GET /api/sessions?sort=oldest must return sessions sorted by lastActivity asc."""
    client = _make_client(two_project_setup["projects_root"])
    response = client.get("/api/sessions?sort=oldest")

    assert response.status_code == 200
    data = response.json()

    activities = [
        datetime.fromisoformat(item["lastActivity"])
        for item in data
        if item.get("lastActivity") is not None
    ]
    assert activities == sorted(activities)


# ---------------------------------------------------------------------------
# Test 8: /health is not regressed
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Test 9: null lastActivity sessions are ALWAYS last regardless of sort order
# ---------------------------------------------------------------------------

def test_null_last_activity_always_sorted_last(
    tmp_path: Path, fixture_projects_root: Path
) -> None:
    """Sessions with lastActivity=null must appear LAST for both sort=recent and sort=oldest.

    Covers D7 state-without-transcript sessions (case where no timestamp can
    be extracted).  Previously the sort placed null items FIRST when
    sort=recent because ``reverse=True`` also inverted the null-rank tuple.

    Setup: three sessions —
      - sid_a: 2026-06-19T10:00:00Z  (most recent)
      - anchor: 2026-06-19T09:00:00Z  (older, also provides cwd for slug resolution)
      - state_sid: no transcript → lastActivity=null
    """
    work = tmp_path / "work"
    proj = work / "null-activity-project"
    proj.mkdir(parents=True, exist_ok=True)
    slug = str(proj).replace("/", "-")

    # Session A — recent timestamp
    sid_a = "aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa"
    _write_jsonl(
        fixture_projects_root / slug / f"{sid_a}.jsonl",
        [
            {
                "type": "user",
                "timestamp": "2026-06-19T10:00:00Z",
                "cwd": str(proj),
                "message": {"role": "user", "content": "recent"},
            }
        ],
    )
    _write_session_started(
        proj,
        {
            "session_id": sid_a,
            "started_at": "2026-06-19T10:00:00Z",
            "cwd": str(proj),
            "pid": 5001,
            "boot_id": "boot-a1",
        },
    )

    # Anchor session — older timestamp, also provides cwd for slug resolution
    anchor = "bbbbbbbb-2222-2222-2222-bbbbbbbbbbbb"
    _write_jsonl(
        fixture_projects_root / slug / f"{anchor}.jsonl",
        [
            {
                "type": "user",
                "timestamp": "2026-06-19T09:00:00Z",
                "cwd": str(proj),
                "message": {"role": "user", "content": "anchor"},
            }
        ],
    )
    _write_session_started(
        proj,
        {
            "session_id": anchor,
            "started_at": "2026-06-19T09:00:00Z",
            "cwd": str(proj),
            "pid": 5002,
            "boot_id": "boot-b2",
        },
    )

    # State-only session — no transcript, so lastActivity=null (D7 case)
    state_sid = "cccccccc-3333-3333-3333-cccccccccccc"
    _write_session_started(
        proj,
        {
            "session_id": state_sid,
            "started_at": "2026-06-19T08:00:00Z",
            "cwd": str(proj),
            "pid": 5003,
            "boot_id": "boot-c3",
        },
    )

    client = _make_client(fixture_projects_root)

    # sort=recent: sid_a first, anchor second, state_sid (null) LAST
    response_recent = client.get("/api/sessions?sort=recent")
    assert response_recent.status_code == 200
    data_recent = response_recent.json()
    ids_recent = [item["sessionId"] for item in data_recent]
    assert state_sid in ids_recent, "state_sid must appear in the list"
    assert ids_recent[-1] == state_sid, (
        f"sort=recent: null lastActivity must be LAST, got order {ids_recent}"
    )

    # sort=oldest: anchor first, sid_a second, state_sid (null) LAST
    response_oldest = client.get("/api/sessions?sort=oldest")
    assert response_oldest.status_code == 200
    data_oldest = response_oldest.json()
    ids_oldest = [item["sessionId"] for item in data_oldest]
    assert state_sid in ids_oldest, "state_sid must appear in the list"
    assert ids_oldest[-1] == state_sid, (
        f"sort=oldest: null lastActivity must be LAST, got order {ids_oldest}"
    )


def test_health_not_regressed() -> None:
    """GET /health must still return 200 after router inclusion."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
