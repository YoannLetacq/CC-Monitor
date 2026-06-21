"""Pydantic response schemas for the F5 report-view feature.

These schemas describe a single parsed agent report (``reports/*__*.md``):
its title, classified verdict, gate results (both the pipe-table and the
section/block forms), and best-effort touched-file list. They live in a
dedicated module (rather than ``orchestration.py``) so the F1/F2 parsers and
the F5 parser can evolve without edit collisions.

All models use camelCase JSON aliases via ``alias_generator`` so the wire
format matches the frontend convention while Python code stays snake_case.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class GateResult(BaseModel):
    """A single quality-gate outcome parsed from a report.

    ``result`` is ``"—"`` (em dash) when the report does not express a clear
    PASS/FAIL for the gate.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    name: str
    result: Literal["PASS", "FAIL", "—"]
    evidence: str | None


class ReportFile(BaseModel):
    """A best-effort touched-file entry parsed from a report.

    ``sign`` is ``'+'`` (added), ``'~'`` (modified), ``'-'`` (removed) when
    derivable, else the empty string.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    sign: str
    name: str


class ReportView(BaseModel):
    """Parsed view of one agent report, or an absence marker.

    When ``available`` is ``False`` the report has not been written yet (the
    agent is running or queued); the remaining fields hold safe defaults.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    role: str
    file_name: str
    title: str | None
    verdict: Literal["pass", "fail", "unknown"]
    verdict_text: str | None
    gates: list[GateResult]
    files: list[ReportFile]
    available: bool
