"""Pure parser turning an agent report (``reports/*__*.md``) into a view.

The parser is read-only and defensive: malformed, truncated, or empty
markdown never raises; it degrades to safe defaults (``verdict="unknown"``,
``gates=[]``, em-dash gate results). Two gate forms coexist in real reports
and are both supported (dispatch §0.3):

* a pipe-table whose PASS/FAIL/— column is **detected** (it may be the 4th
  or 5th column depending on whether the table carries a leading ``#``
  index), never assumed by index; and
* sections (``### name``) followed by fenced blocks, from which PASS is
  deduced softly (``10.00/10`` / ``passed`` / ``All checks passed``).

Four verdict spellings are recognised and classified by keyword
(``PASS``/``APPROVE``/``CLEAR`` → pass, ``FAIL``/``REJECT``/``BLOCK`` →
fail, else unknown), preserving the raw one-line verdict text.
"""

import logging
import re
from pathlib import Path
from typing import Literal

from app.schemas.reports import GateResult, ReportFile, ReportView

logger = logging.getLogger(__name__)

_EM_DASH: str = "—"
_ROLE_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_-]+$")
_H1_PATTERN: re.Pattern[str] = re.compile(r"^#\s+(.+?)\s*$")
_SECTION_PATTERN: re.Pattern[str] = re.compile(r"^#{2,6}\s+(.+?)\s*$")
_RESULT_TOKENS: frozenset[str] = frozenset({"PASS", "FAIL", _EM_DASH})
_PASS_KEYWORDS: tuple[str, ...] = ("PASS", "APPROVE", "CLEAR")
_FAIL_KEYWORDS: tuple[str, ...] = ("FAIL", "REJECT", "BLOCK")
_PASS_HINTS: tuple[str, ...] = ("10.00/10", "passed", "all checks passed")
_FILE_LINE_PATTERN: re.Pattern[str] = re.compile(r"^[-*]\s+`?\s*([+~-])\s+([^`]+?)`?\s*$")

Verdict = Literal["pass", "fail", "unknown"]


def role_of(file_name: str) -> str:
    """Return the role segment after ``__`` (stem without extension).

    Falls back to the whole stem when no ``__`` separator is present.
    """
    stem: str = Path(file_name).stem
    if "__" in stem:
        return stem.split("__", 1)[1]
    return stem


def extract_title(md_text: str, file_name: str) -> str | None:
    """Return the first ``# H1`` heading, falling back to ``file_name``."""
    for line in md_text.splitlines():
        match = _H1_PATTERN.match(line)
        if match is not None:
            return match.group(1)
    return file_name


def _classify_verdict(text: str) -> Verdict:
    """Classify a one-line verdict by keyword (pass wins over fail ties)."""
    upper: str = text.upper()
    if any(keyword in upper for keyword in _PASS_KEYWORDS):
        return "pass"
    if any(keyword in upper for keyword in _FAIL_KEYWORDS):
        return "fail"
    return "unknown"


def _verdict_from_inline(md_text: str) -> str | None:
    """Return the verdict text from a bold inline form, if present.

    Handles both ``**Verdict: X ...**`` (value inside the bold span) and
    ``**Verdict:** X ...`` (value after the bold label).
    """
    inside = re.search(
        r"\*\*\s*verdict\s*:\s*(.+?)\s*\*\*(.*)$", md_text, re.IGNORECASE | re.MULTILINE
    )
    if inside is not None:
        return (inside.group(1).strip() + " " + inside.group(2).strip()).strip()
    after = re.search(
        r"\*\*\s*verdict\s*:?\s*\*\*\s*[:\-—]?\s*(.+)$",
        md_text,
        re.IGNORECASE | re.MULTILINE,
    )
    if after is not None:
        return after.group(1).strip()
    return None


def _verdict_from_heading(lines: list[str]) -> str | None:
    """Return the verdict text from a ``## Verdict`` / ``## VERDICT: X`` form."""
    for index, line in enumerate(lines):
        match = re.match(r"^#{2,6}\s+verdict\s*:?\s*(.*)$", line, re.IGNORECASE)
        if match is None:
            continue
        inline: str = match.group(1).strip()
        if inline:
            return inline
        return _first_nonblank(lines[index + 1 :])
    return None


def _first_nonblank(lines: list[str]) -> str | None:
    """Return the first non-blank stripped line, else ``None``."""
    for line in lines:
        stripped: str = line.strip()
        if stripped:
            return stripped
    return None


def extract_verdict(md_text: str) -> tuple[Verdict, str | None]:
    """Return ``(verdict, verdict_text)`` from any of the four forms.

    The raw one-line verdict text is preserved; the class is derived from
    keywords. No marker at all yields ``("unknown", None)``.
    """
    text = _verdict_from_inline(md_text) or _verdict_from_heading(md_text.splitlines())
    if text is None:
        return "unknown", None
    return _classify_verdict(text), text


def _split_row(line: str) -> list[str] | None:
    """Split a markdown pipe-table row into trimmed cells, else ``None``."""
    stripped: str = line.strip()
    if not stripped.startswith("|"):
        return None
    body: str = stripped.strip("|")
    return [cell.strip() for cell in body.split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    """Return whether every cell is a markdown header separator (``---``)."""
    return all(set(cell) <= {"-", ":"} and cell for cell in cells)


def _result_column(rows: list[list[str]]) -> int | None:
    """Detect the column index holding PASS/FAIL/— tokens across data rows."""
    if not rows:
        return None
    width: int = min(len(row) for row in rows)
    for col in range(width):
        tokens = {_strip_ticks(row[col]).upper() for row in rows}
        if tokens & {"PASS", "FAIL"} or _EM_DASH in {row[col].strip() for row in rows}:
            if tokens - {"PASS", "FAIL", _EM_DASH, ""} == set():
                return col
    return None


def _strip_ticks(cell: str) -> str:
    """Strip surrounding backticks and whitespace from a table cell."""
    return cell.strip().strip("`").strip()


def _normalize_result(cell: str) -> Literal["PASS", "FAIL", "—"]:
    """Map a result cell to one of ``PASS``/``FAIL``/``—`` (em dash)."""
    token: str = _strip_ticks(cell).upper()
    if token == "PASS":
        return "PASS"
    if token == "FAIL":
        return "FAIL"
    return _EM_DASH


def _collect_pipe_tables(md_text: str) -> list[list[list[str]]]:
    """Group consecutive pipe-table rows into tables (header + data rows)."""
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in md_text.splitlines():
        cells = _split_row(line)
        if cells is None:
            if current:
                tables.append(current)
                current = []
            continue
        current.append(cells)
    if current:
        tables.append(current)
    return tables


def _gates_from_table(table: list[list[str]]) -> list[GateResult]:
    """Extract gate results from one pipe table, or ``[]`` if it has none."""
    data_rows: list[list[str]] = [row for row in table if not _is_separator_row(row)]
    if len(data_rows) < 2:
        return []
    _header, *rows = data_rows
    result_col: int | None = _result_column(rows)
    if result_col is None:
        return []
    name_col: int = _name_column(rows, result_col)
    gates: list[GateResult] = []
    for row in rows:
        if len(row) <= result_col:
            continue
        name: str = _strip_ticks(row[name_col]) if len(row) > name_col else ""
        evidence: str | None = _row_evidence(row, result_col)
        gates.append(
            GateResult(name=name, result=_normalize_result(row[result_col]), evidence=evidence)
        )
    return gates


def _name_column(rows: list[list[str]], result_col: int) -> int:
    """Pick the gate-name column: column 0, unless it is a numeric index.

    Five-column tables lead with a ``#`` index column; in that case the name
    lives in column 1. The chosen column is always left of the result column.
    """
    if result_col > 1 and all(_strip_ticks(row[0]).isdigit() for row in rows if row):
        return 1
    return 0


def _row_evidence(row: list[str], result_col: int) -> str | None:
    """Return the cell just after the result column as evidence, else ``None``."""
    if len(row) > result_col + 1:
        return _strip_ticks(row[result_col + 1]) or None
    return None


def _gates_from_sections(md_text: str) -> list[GateResult]:
    """Deduce gate results from ``### name`` sections and their block text."""
    gates: list[GateResult] = []
    lines: list[str] = md_text.splitlines()
    for index, line in enumerate(lines):
        match = _SECTION_PATTERN.match(line)
        if match is None:
            continue
        name: str = match.group(1).strip()
        if name.lower().startswith("verdict"):
            continue
        body: str = _section_body(lines, index + 1)
        result = "PASS" if _looks_passed(body) else _EM_DASH
        gates.append(GateResult(name=name, result=result, evidence=None))
    return gates


def _section_body(lines: list[str], start: int) -> str:
    """Return the text of a section until the next heading."""
    collected: list[str] = []
    for line in lines[start:]:
        if _SECTION_PATTERN.match(line) is not None:
            break
        collected.append(line)
    return "\n".join(collected)


def _looks_passed(body: str) -> bool:
    """Return whether a section body softly indicates a passing gate."""
    lowered: str = body.lower()
    return any(hint in lowered for hint in _PASS_HINTS)


def extract_gates(md_text: str) -> list[GateResult]:
    """Return gate results, preferring the pipe-table form over sections.

    A gate without a clear result is reported as ``"—"``; absent gate
    evidence yields an empty list.
    """
    for table in _collect_pipe_tables(md_text):
        gates = _gates_from_table(table)
        if gates:
            return gates
    return _gates_from_sections(md_text)


def extract_files(md_text: str) -> list[ReportFile]:
    """Return best-effort touched files from ``+``/``~``/``-`` bullet lines."""
    files: list[ReportFile] = []
    for line in md_text.splitlines():
        match = _FILE_LINE_PATTERN.match(line.strip())
        if match is not None:
            files.append(ReportFile(sign=match.group(1), name=match.group(2).strip()))
    return files


def parse_report(md_text: str, file_name: str) -> ReportView:
    """Parse markdown into an available :class:`ReportView`."""
    verdict, verdict_text = extract_verdict(md_text)
    return ReportView(
        role=role_of(file_name),
        file_name=file_name,
        title=extract_title(md_text, file_name),
        verdict=verdict,
        verdict_text=verdict_text,
        gates=extract_gates(md_text),
        files=extract_files(md_text),
        available=True,
    )


def list_reports(reports_dir: Path) -> list[str]:
    """Return the names of ``*__*.md`` report files in ``reports_dir``."""
    try:
        return sorted(f.name for f in reports_dir.glob("*__*.md") if f.is_file())
    except OSError as exc:
        logger.debug("cannot list reports in %s: %s", reports_dir, exc)
        return []


def _unavailable(role: str) -> ReportView:
    """Return a safe :class:`ReportView` marking the report as absent."""
    return ReportView(
        role=role,
        file_name="",
        title=None,
        verdict="unknown",
        verdict_text=None,
        gates=[],
        files=[],
        available=False,
    )


def load_report(reports_dir: Path, role: str) -> ReportView:
    """Load the report for ``role`` (``*__{role}.md``), or an absence marker.

    ``role`` is whitelisted to ``[A-Za-z0-9_-]`` so it can never escape the
    reports directory (CWE-22); a non-matching or missing report yields
    ``available=False`` without raising.
    """
    if _ROLE_PATTERN.match(role) is None:
        return _unavailable(role)
    for name in list_reports(reports_dir):
        if role_of(name) == role:
            return _read_and_parse(reports_dir / name, name, role)
    return _unavailable(role)


def _read_and_parse(path: Path, name: str, role: str) -> ReportView:
    """Read a report file and parse it, degrading to absence on read error."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            md_text = handle.read()
    except OSError as exc:
        logger.debug("cannot read report %s: %s", path, exc)
        return _unavailable(role)
    return parse_report(md_text, name)
