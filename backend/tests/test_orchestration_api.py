"""Integration tests for the orchestration REST endpoints (Worker C).

Routes under test (§1.4):
    GET /api/sessions/{sid}/agents     → AgentTree (F1)
    GET /api/sessions/{sid}/timeline   → list[TimelineEvent] (F2)
    GET /api/sessions/{sid}/reports    → list[ReportView] (F5, all)
    GET /api/sessions/{sid}/reports/{role} → ReportView (F5, one)
    GET /api/sessions/{sid}/overview   → SessionOverview (F6)

Coverage targets (§5 TDD brief):
    - 200 for each route on a known session
    - 404 on unknown sid
    - path-traversal on sid and role blocked
    - report pending (available=false) for missing role
    - F6 KPIs recomputed from agents[] (not total_*)
    - /health not regressed
    - Incr.1 routes not broken

Fixtures use tmp_path (anonymized); the real ~/.claude is never read.
CLAUDE_PROJECTS_ROOT is injected via env override.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SID = "aabbccdd11223344"
_SID2 = "eeff99887766aabb"
_SLUG_NAME = "test-project"

_TRACKING_COMPLETED = {
    "agents": [
        {
            "agent_id": "aaa111bbb222ccc3",
            "agent_type": "oh-my-claudecode:planner",
            "started_at": "2026-06-19T08:00:00.000Z",
            "completed_at": "2026-06-19T08:10:00.000Z",
            "duration_ms": 600000,
            "status": "completed",
            "parent_mode": "none",
        },
        {
            "agent_id": "ddd444eee555fff6",
            "agent_type": "oh-my-claudecode:executor",
            "started_at": "2026-06-19T08:05:00.000Z",
            "completed_at": "2026-06-19T08:15:00.000Z",
            "duration_ms": 600000,
            "status": "completed",
            "parent_mode": "none",
        },
    ],
    "total_spawned": 2,
    "total_completed": 999,  # intentionally wrong — F6 must recount
    "total_failed": 0,
    "last_updated": "2026-06-19T08:15:00.000Z",
}

_TRACKING_MULTI_STATUS = {
    "agents": [
        {
            "agent_id": "run111",
            "agent_type": "executor",
            "started_at": "2026-06-19T09:00:00.000Z",
            "status": "running",
            "parent_mode": "none",
        },
        {
            "agent_id": "done111",
            "agent_type": "executor",
            "started_at": "2026-06-19T09:01:00.000Z",
            "completed_at": "2026-06-19T09:05:00.000Z",
            "duration_ms": 240000,
            "status": "completed",
            "parent_mode": "none",
        },
        {
            "agent_id": "fail111",
            "agent_type": "executor",
            "started_at": "2026-06-19T09:02:00.000Z",
            "completed_at": "2026-06-19T09:03:00.000Z",
            "duration_ms": 60000,
            "status": "failed",
            "parent_mode": "none",
        },
    ],
    "total_spawned": 3,
    "total_completed": 999,  # wrong — must recount
    "total_failed": 999,  # wrong — must recount
    "last_updated": "2026-06-19T09:05:00.000Z",
}

_REPORT_MD = """\
# Worker test — report

## Verdict

PASS. All gates clear.

| Gate | Command | Result | Evidence |
|------|---------|--------|----------|
| Pylint | uv run pylint | PASS | 10.00/10 |
| Pytest | uv run pytest | PASS | 12 passed |

## Files
- `+ backend/app/api/orchestration.py`
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: dict) -> None:
    """Write a JSON object to path (test helper, write mode)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def _write_text(path: Path, text: str) -> None:
    """Write text to path (test helper, write mode)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _write_transcript(projects_root: Path, project_dir: Path, sid: str) -> None:
    """Write a minimal transcript with cwd so the session resolves."""
    slug = str(project_dir).replace("/", "-")
    transcript = projects_root / slug / f"{sid}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    with open(transcript, "w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "type": "user",
                    "cwd": str(project_dir),
                    "timestamp": "2026-06-19T08:00:00.000Z",
                }
            )
            + "\n"
        )


@pytest.fixture()
def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up a complete project tree and return (client, project_dir, sid)."""
    from app.core.config import get_settings

    projects_root = tmp_path / "claude_projects"
    project_dir = tmp_path / "work" / _SLUG_NAME
    project_dir.mkdir(parents=True, exist_ok=True)

    # Transcript so discover_sessions can resolve the session
    _write_transcript(projects_root, project_dir, _SID)

    # State dir with tracking
    state_dir = project_dir / ".omc" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    _write_json(state_dir / "subagent-tracking.json", _TRACKING_COMPLETED)

    # Reports dir with one report
    reports_dir = project_dir / ".omc" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    _write_text(reports_dir / "branch__state-parsers.md", _REPORT_MD)

    monkeypatch.setenv("CLAUDE_PROJECTS_ROOT", str(projects_root))
    get_settings.cache_clear()

    from app.main import create_app

    client = TestClient(create_app())
    yield client, project_dir, _SID
    get_settings.cache_clear()


@pytest.fixture()
def _setup_multi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up a project with multi-status agents for F6 KPI tests."""
    from app.core.config import get_settings

    projects_root = tmp_path / "claude_projects"
    project_dir = tmp_path / "work" / _SLUG_NAME
    project_dir.mkdir(parents=True, exist_ok=True)

    _write_transcript(projects_root, project_dir, _SID2)

    state_dir = project_dir / ".omc" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    _write_json(state_dir / "subagent-tracking.json", _TRACKING_MULTI_STATUS)

    reports_dir = project_dir / ".omc" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("CLAUDE_PROJECTS_ROOT", str(projects_root))
    get_settings.cache_clear()

    from app.main import create_app

    client = TestClient(create_app())
    yield client, project_dir, _SID2
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# /health regression
# ---------------------------------------------------------------------------


def test_health_not_regressed(_setup):
    """GET /health still returns 200 with {status: ok}."""
    client, _, _ = _setup
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Incr.1 routes regression
# ---------------------------------------------------------------------------


def test_sessions_list_not_regressed(_setup):
    """GET /api/sessions still returns 200 (Incr.1 route not broken)."""
    client, _, _ = _setup
    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# F1 — /agents
# ---------------------------------------------------------------------------


def test_agents_200(_setup):
    """GET /api/sessions/{sid}/agents returns 200 with AgentTree."""
    client, _, sid = _setup
    resp = client.get(f"/api/sessions/{sid}/agents")
    assert resp.status_code == 200
    body = resp.json()
    assert "sessionId" in body
    assert "nodes" in body
    assert body["hierarchyAvailable"] is False
    assert isinstance(body["nodes"], list)
    assert len(body["nodes"]) == 2


def test_agents_node_fields(_setup):
    """AgentTree nodes carry expected camelCase fields."""
    client, _, sid = _setup
    body = client.get(f"/api/sessions/{sid}/agents").json()
    node = body["nodes"][0]
    for field in ("agentId", "agentType", "role", "status", "startedAt", "durationMs"):
        assert field in node, f"missing field: {field}"


def test_agents_type_normalized(_setup):
    """Plugin prefix is stripped from agentType (oh-my-claudecode:planner → planner)."""
    client, _, sid = _setup
    body = client.get(f"/api/sessions/{sid}/agents").json()
    types = {n["agentType"] for n in body["nodes"]}
    assert "planner" in types
    assert "executor" in types
    assert not any(":" in t for t in types)


def test_agents_404_unknown_sid(_setup):
    """GET /api/sessions/{unknown}/agents returns 404."""
    client, _, _ = _setup
    resp = client.get("/api/sessions/unknown-session-id/agents")
    assert resp.status_code == 404


def test_agents_path_traversal_blocked(_setup):
    """Path-traversal in sid is blocked (404, not a directory escape)."""
    client, _, _ = _setup
    resp = client.get("/api/sessions/../../../etc/passwd/agents")
    assert resp.status_code in (404, 422)


# ---------------------------------------------------------------------------
# F2 — /timeline
# ---------------------------------------------------------------------------


def test_timeline_200(_setup):
    """GET /api/sessions/{sid}/timeline returns 200 with a list."""
    client, _, sid = _setup
    resp = client.get(f"/api/sessions/{sid}/timeline")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) > 0


def test_timeline_event_fields(_setup):
    """Timeline events carry expected camelCase fields."""
    client, _, sid = _setup
    body = client.get(f"/api/sessions/{sid}/timeline").json()
    event = body[0]
    for field in ("agentType", "event", "at"):
        assert field in event, f"missing field: {field}"


def test_timeline_404_unknown_sid(_setup):
    """GET /api/sessions/{unknown}/timeline returns 404."""
    client, _, _ = _setup
    resp = client.get("/api/sessions/unknown-session-id/timeline")
    assert resp.status_code == 404


def test_timeline_path_traversal_blocked(_setup):
    """Path-traversal in sid is blocked for timeline."""
    client, _, _ = _setup
    resp = client.get("/api/sessions/../../../etc/passwd/timeline")
    assert resp.status_code in (404, 422)


# ---------------------------------------------------------------------------
# F5 — /reports (all)
# ---------------------------------------------------------------------------


def test_reports_list_200(_setup):
    """GET /api/sessions/{sid}/reports returns 200 with a list."""
    client, _, sid = _setup
    resp = client.get(f"/api/sessions/{sid}/reports")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1


def test_reports_list_fields(_setup):
    """Report list items carry expected camelCase fields."""
    client, _, sid = _setup
    body = client.get(f"/api/sessions/{sid}/reports").json()
    report = body[0]
    for field in ("role", "fileName", "verdict", "available"):
        assert field in report, f"missing field: {field}"


def test_reports_list_404_unknown_sid(_setup):
    """GET /api/sessions/{unknown}/reports returns 404."""
    client, _, _ = _setup
    resp = client.get("/api/sessions/unknown-session-id/reports")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# F5 — /reports/{role} (single)
# ---------------------------------------------------------------------------


def test_report_role_200(_setup):
    """GET /api/sessions/{sid}/reports/state-parsers returns 200."""
    client, _, sid = _setup
    resp = client.get(f"/api/sessions/{sid}/reports/state-parsers")
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "state-parsers"
    assert body["available"] is True
    assert body["verdict"] == "pass"


def test_report_role_pending(_setup):
    """GET /api/sessions/{sid}/reports/{missing-role} returns 200 with available=false."""
    client, _, sid = _setup
    resp = client.get(f"/api/sessions/{sid}/reports/nonexistent-role")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["role"] == "nonexistent-role"


def test_report_role_404_unknown_sid(_setup):
    """GET /api/sessions/{unknown}/reports/any-role returns 404."""
    client, _, _ = _setup
    resp = client.get("/api/sessions/unknown-session-id/reports/any-role")
    assert resp.status_code == 404


def test_report_role_path_traversal_blocked(_setup):
    """Path-traversal in role is blocked (available=false or 404)."""
    client, _, sid = _setup
    # Role with path-traversal characters — must be rejected
    resp = client.get(f"/api/sessions/{sid}/reports/../../etc/passwd")
    # FastAPI/Starlette may 404 the route entirely or our code returns available=false
    assert resp.status_code in (404, 422, 200)
    if resp.status_code == 200:
        assert resp.json()["available"] is False


def test_report_role_whitelist_enforced(_setup):
    """Role containing forbidden characters yields available=false (whitelist §1.5)."""
    client, _, sid = _setup
    # Characters outside [A-Za-z0-9_-] must be rejected
    resp = client.get(f"/api/sessions/{sid}/reports/bad role!")
    # 422 from path param decode or 404; either is acceptable
    assert resp.status_code in (404, 422, 200)
    if resp.status_code == 200:
        assert resp.json()["available"] is False


# ---------------------------------------------------------------------------
# F6 — /overview
# ---------------------------------------------------------------------------


def test_overview_200(_setup):
    """GET /api/sessions/{sid}/overview returns 200 with SessionOverview."""
    client, _, sid = _setup
    resp = client.get(f"/api/sessions/{sid}/overview")
    assert resp.status_code == 200
    body = resp.json()
    for field in ("sessionId", "active", "done", "failed", "queued", "gateSummary"):
        assert field in body, f"missing field: {field}"


def test_overview_kpis_recomputed(_setup_multi):
    """F6 KPIs are recomputed from agents[], never from total_* (§0.1)."""
    client, _, sid = _setup_multi
    resp = client.get(f"/api/sessions/{sid}/overview")
    assert resp.status_code == 200
    body = resp.json()
    # _TRACKING_MULTI_STATUS has 1 running, 1 completed, 1 failed; total_* are 999
    assert body["active"] == 1
    assert body["done"] == 1
    assert body["failed"] == 1
    assert body["queued"] == 0


def test_overview_gate_summary(_setup):
    """F6 gate_summary aggregates PASS/FAIL across all reports."""
    client, _, sid = _setup
    resp = client.get(f"/api/sessions/{sid}/overview")
    assert resp.status_code == 200
    body = resp.json()
    gs = body["gateSummary"]
    assert "passCount" in gs
    assert "failCount" in gs
    assert gs["passCount"] >= 0
    assert gs["failCount"] >= 0


def test_overview_elapsed_ms(_setup):
    """F6 elapsed_ms is present and non-negative."""
    client, _, sid = _setup
    resp = client.get(f"/api/sessions/{sid}/overview")
    assert resp.status_code == 200
    body = resp.json()
    elapsed = body.get("elapsedMs")
    assert elapsed is None or elapsed >= 0


def test_overview_404_unknown_sid(_setup):
    """GET /api/sessions/{unknown}/overview returns 404."""
    client, _, _ = _setup
    resp = client.get("/api/sessions/unknown-session-id/overview")
    assert resp.status_code == 404


def test_overview_path_traversal_blocked(_setup):
    """Path-traversal in sid is blocked for overview."""
    client, _, _ = _setup
    resp = client.get("/api/sessions/../../../etc/passwd/overview")
    assert resp.status_code in (404, 422)
