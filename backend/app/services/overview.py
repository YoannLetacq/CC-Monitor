"""Session overview builder (F6) — recomputes KPIs from agents[].

The ``total_*`` counters in ``subagent-tracking.json`` are incoherent on real
data (e.g. ``total_completed=160`` for 19 agents); this module **always**
recounts from the ``AgentNodeData`` list produced by
``app.services.orchestration.read_tracking``, never from the tracking
aggregates.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from app.schemas.orchestration import GateSummary, SessionOverview
from app.schemas.reports import GateResult, ReportView
from app.services.orchestration import AgentNodeData, read_tracking
from app.services.reports import load_all_reports

logger = logging.getLogger(__name__)

_STATUS_DONE: frozenset[str] = frozenset({"completed"})
_STATUS_FAILED: frozenset[str] = frozenset({"failed", "blocked"})
_STATUS_ACTIVE: frozenset[str] = frozenset({"running"})


def _count_statuses(nodes: list[AgentNodeData]) -> tuple[int, int, int, int]:
    """Return (active, done, failed, queued) recounted from nodes.

    ``queued`` = nodes whose status is not active/done/failed (e.g. ``unknown``
    or any other value not in the three known buckets).
    """
    active = done = failed = queued = 0
    for node in nodes:
        if node.status in _STATUS_ACTIVE:
            active += 1
        elif node.status in _STATUS_DONE:
            done += 1
        elif node.status in _STATUS_FAILED:
            failed += 1
        else:
            queued += 1
    return active, done, failed, queued


def _elapsed_ms(nodes: list[AgentNodeData]) -> int | None:
    """Return ``now - min(started_at)`` in milliseconds, or ``None``.

    Uses UTC now; a missing or all-None started_at set yields ``None``.
    """
    starts = [n.started_at for n in nodes if n.started_at is not None]
    if not starts:
        return None
    earliest = min(starts)
    now = datetime.now(tz=timezone.utc)
    # Ensure both are offset-aware for subtraction
    if earliest.tzinfo is None:
        earliest = earliest.replace(tzinfo=timezone.utc)
    delta_ms = int((now - earliest).total_seconds() * 1000)
    return max(0, delta_ms)


def _gate_summary(reports: list[ReportView]) -> tuple[int, int]:
    """Return (pass_count, fail_count) aggregated across all report gates."""
    pass_count = fail_count = 0
    for report in reports:
        if not report.available:
            continue
        gate: GateResult
        for gate in report.gates:
            if gate.result == "PASS":
                pass_count += 1
            elif gate.result == "FAIL":
                fail_count += 1
    return pass_count, fail_count


def build_overview(
    state_dir: Path,
    reports_dir: Path,
    session_id: str,
) -> SessionOverview:
    """Build the F6 session overview, recomputing all KPIs from raw data.

    ``active``/``done``/``failed``/``queued`` are recounted from the
    ``agents[]`` list in tracking; ``elapsed_ms`` is the span from the
    earliest ``started_at`` to now; ``gate_summary`` aggregates PASS/FAIL
    across all available reports.
    """
    logger.debug("building overview for session %s", session_id)
    nodes = read_tracking(state_dir)
    active, done, failed, queued = _count_statuses(nodes)
    elapsed = _elapsed_ms(nodes)
    reports = load_all_reports(reports_dir)
    pass_count, fail_count = _gate_summary(reports)
    return SessionOverview(
        session_id=session_id,
        active=active,
        done=done,
        failed=failed,
        queued=queued,
        elapsed_ms=elapsed,
        gate_summary=GateSummary(pass_count=pass_count, fail_count=fail_count),
    )
