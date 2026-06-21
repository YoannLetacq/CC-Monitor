"""Unit tests for the pure F5 report parser (``app.services.reports``).

All markdown inputs are anonymized fixtures replicating the real report
forms described in the dispatch (§0.3): pipe-table gates with four or five
columns, section/block gates, and the four verdict spellings. The real
``reports/*.md`` files are never read here.
"""

from pathlib import Path

from app.services.reports import (
    extract_files,
    extract_gates,
    extract_title,
    extract_verdict,
    list_reports,
    load_all_reports,
    load_report,
    parse_report,
    role_of,
)

_PIPE_FOUR_COL = """# Verify Report

## Gate Table

| Gate | Command | Result | Evidence |
|------|---------|--------|----------|
| Ruff | `uv run ruff check .` | PASS | `All checks passed!` |
| Pytest | `uv run pytest` | FAIL | `1 failed in 0.10s` |
| Mypy | `uv run mypy .` | — | not run |

## Verdict

PASS. All checks passed cleanly.
"""

_PIPE_FIVE_COL = """# Verify Report

## Gate Table

| # | Gate | Command | Result | Evidence |
|---|------|---------|--------|----------|
| 1 | Ruff | `uv run ruff check .` | PASS | `All checks passed!` |
| 2 | Pytest | `uv run pytest` | PASS | `27 passed in 0.43s` |
"""

_SECTION_BLOCKS = """# Config Report

### pylint
```
Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)
```

### pytest
```
27 passed in 0.43s
```

## Verdict

PASS. Four settings added.
"""


def test_extract_title_present() -> None:
    """The first H1 becomes the title."""
    assert extract_title("# My Title\n\nbody", "ignored.md") == "My Title"


def test_extract_title_absent_falls_back_to_file_name() -> None:
    """With no H1 the file name is used as the title."""
    assert extract_title("no heading here", "auth__verify.md") == "auth__verify.md"


def test_verdict_section_heading_form() -> None:
    """``## Verdict`` then a line below classifies as pass and keeps text."""
    verdict, text = extract_verdict(_PIPE_FOUR_COL)
    assert verdict == "pass"
    assert text is not None and text.startswith("PASS")


def test_verdict_inline_bold_with_value_inside() -> None:
    """``**Verdict: FAIL**`` inline form classifies as fail."""
    verdict, text = extract_verdict("**Verdict: FAIL** — broken build")
    assert verdict == "fail"
    assert text == "FAIL — broken build"


def test_verdict_bold_label_then_value() -> None:
    """``**Verdict:** PASS`` form classifies as pass."""
    verdict, text = extract_verdict("**Verdict:** PASS — looks good")
    assert verdict == "pass"
    assert text == "PASS — looks good"


def test_verdict_title_line_clear_to_proceed_is_pass() -> None:
    """``## VERDICT: CLEAR-TO-PROCEED`` classifies as pass via CLEAR."""
    verdict, text = extract_verdict("## VERDICT: CLEAR-TO-PROCEED")
    assert verdict == "pass"
    assert text == "CLEAR-TO-PROCEED"


def test_verdict_reject_is_fail() -> None:
    """A REJECT keyword classifies as fail."""
    verdict, _text = extract_verdict("## VERDICT: REJECT")
    assert verdict == "fail"


def test_verdict_unknown_keyword() -> None:
    """A verdict without known keywords classifies as unknown."""
    verdict, text = extract_verdict("**Verdict:** NEEDS-DISCUSSION")
    assert verdict == "unknown"
    assert text == "NEEDS-DISCUSSION"


def test_verdict_absent_is_unknown() -> None:
    """No verdict marker at all yields unknown and no text."""
    verdict, text = extract_verdict("# Title\n\njust prose")
    assert verdict == "unknown"
    assert text is None


def test_gates_pipe_four_columns() -> None:
    """Four-column pipe table: Result column detected, not fixed index."""
    gates = extract_gates(_PIPE_FOUR_COL)
    by_name = {g.name: g.result for g in gates}
    assert by_name["Ruff"] == "PASS"
    assert by_name["Pytest"] == "FAIL"
    assert by_name["Mypy"] == "—"


def test_gates_pipe_five_columns() -> None:
    """Five-column pipe table: Result column detected past the index column."""
    gates = extract_gates(_PIPE_FIVE_COL)
    by_name = {g.name: g.result for g in gates}
    assert by_name == {"Ruff": "PASS", "Pytest": "PASS"}


def test_gates_pipe_evidence_not_mistaken_for_result() -> None:
    """The PASS in the Evidence column must not flip a — result."""
    md = """| Gate | Result | Evidence |
|------|--------|----------|
| Lint | — | nothing PASS-like to report |
"""
    gates = extract_gates(md)
    assert gates[0].result == "—"


def test_gates_section_blocks_deduce_pass() -> None:
    """Section form: ``10.00/10`` and ``passed`` deduce PASS."""
    gates = extract_gates(_SECTION_BLOCKS)
    by_name = {g.name: g.result for g in gates}
    assert by_name["pylint"] == "PASS"
    assert by_name["pytest"] == "PASS"


def test_gates_absent_is_empty() -> None:
    """No gate evidence at all yields an empty list."""
    assert not extract_gates("# Title\n\nno gates here")


def test_extract_files_best_effort_signs() -> None:
    """Touched-file lines with +/~/- signs are parsed best-effort."""
    md = """## Files
- `+ app/new.py`
- `~ app/edit.py`
- `- app/old.py`
"""
    files = extract_files(md)
    signs = {f.name: f.sign for f in files}
    assert signs.get("app/new.py") == "+"
    assert signs.get("app/edit.py") == "~"
    assert signs.get("app/old.py") == "-"


def test_parse_report_pipe_form_end_to_end() -> None:
    """A full pipe-table report parses into a coherent ReportView."""
    view = parse_report(_PIPE_FOUR_COL, "auth-sessions__verify.md")
    assert view.available is True
    assert view.role == "verify"
    assert view.title == "Verify Report"
    assert view.verdict == "pass"
    assert len(view.gates) == 3


def test_parse_report_section_form_end_to_end() -> None:
    """A full section/block report parses into a coherent ReportView."""
    view = parse_report(_SECTION_BLOCKS, "feature__config.md")
    assert view.role == "config"
    assert view.verdict == "pass"
    assert {g.name for g in view.gates} == {"pylint", "pytest"}


def test_parse_report_empty_markdown_is_safe() -> None:
    """Empty markdown yields a safe ReportView (unknown verdict, no gates)."""
    view = parse_report("", "x__role.md")
    assert view.available is True
    assert view.verdict == "unknown"
    assert view.gates == []
    assert view.title == "x__role.md"


def test_parse_report_truncated_markdown_is_safe() -> None:
    """A truncated table does not crash and yields a safe view."""
    view = parse_report("# T\n\n| Gate | Result |\n|---", "x__role.md")
    assert view.verdict == "unknown"
    assert isinstance(view.gates, list)


def test_role_of_extracts_segment_after_double_underscore() -> None:
    """``role_of`` returns the segment after ``__`` without the extension."""
    assert role_of("auth-sessions__security-review.md") == "security-review"


def test_role_of_no_separator_returns_stem() -> None:
    """Without a ``__`` separator the file stem is returned."""
    assert role_of("plainfile.md") == "plainfile"


def test_list_reports_globs_double_underscore(tmp_path: Path) -> None:
    """``list_reports`` returns only ``*__*.md`` file names."""
    (tmp_path / "a__verify.md").write_text("x", encoding="utf-8")
    (tmp_path / "b__review.md").write_text("y", encoding="utf-8")
    (tmp_path / "README.md").write_text("z", encoding="utf-8")
    names = set(list_reports(tmp_path))
    assert names == {"a__verify.md", "b__review.md"}


def test_list_reports_missing_dir_is_empty(tmp_path: Path) -> None:
    """A missing reports directory yields an empty list, not an error."""
    assert list_reports(tmp_path / "nope") == []


def test_load_report_present(tmp_path: Path) -> None:
    """An existing report for a role loads as available with parsed fields."""
    (tmp_path / "branch__verify.md").write_text(_PIPE_FOUR_COL, encoding="utf-8")
    view = load_report(tmp_path, "verify")
    assert view.available is True
    assert view.verdict == "pass"


def test_load_report_absent_is_unavailable(tmp_path: Path) -> None:
    """A missing report yields ``available=False`` without raising."""
    view = load_report(tmp_path, "verify")
    assert view.available is False
    assert view.role == "verify"
    assert view.gates == []


def test_load_report_rejects_traversal_role(tmp_path: Path) -> None:
    """A role outside the whitelist resolves to an unavailable view."""
    view = load_report(tmp_path, "../secret")
    assert view.available is False


# ---------------------------------------------------------------------------
# MEDIUM-1: inline verdict in prose must not override a real ## Verdict heading
# ---------------------------------------------------------------------------

_PROSE_INLINE_THEN_HEADING = """# Worker Report

Some context: The **verdict: pending** review continues.
More prose here that mentions verdict in passing.

## Verdict

FAIL. Gates did not pass.
"""

_INLINE_ANCHORED_SOLO = """# Worker Report

**Verdict: PASS** — all checks green.
"""

_INLINE_AFTER_LABEL_ANCHORED = """# Worker Report

**Verdict:** APPROVE — everything looks good.
"""


def test_verdict_prose_inline_ignored_when_heading_present() -> None:
    """Inline bold verdict buried in prose must not shadow a real ## Verdict heading."""
    verdict, text = extract_verdict(_PROSE_INLINE_THEN_HEADING)
    assert verdict == "fail", (
        "heading ## Verdict must take priority over mid-prose **verdict: pending**"
    )
    assert text is not None and "FAIL" in text.upper()


def test_verdict_inline_anchored_at_line_start_is_captured() -> None:
    """Inline **Verdict: PASS** anchored at the start of a line is captured as fallback."""
    verdict, text = extract_verdict(_INLINE_ANCHORED_SOLO)
    assert verdict == "pass"
    assert text is not None and "PASS" in text.upper()


def test_verdict_inline_after_label_anchored_is_captured() -> None:
    """Inline **Verdict:** APPROVE anchored at line start is captured as fallback."""
    verdict, text = extract_verdict(_INLINE_AFTER_LABEL_ANCHORED)
    assert verdict == "pass"
    assert text is not None


# ---------------------------------------------------------------------------
# MEDIUM-2: load_all_reports must not lose reports with duplicate roles
# ---------------------------------------------------------------------------


def test_load_all_reports_no_loss_on_duplicate_role(tmp_path: Path) -> None:
    """Two files sharing the same role must both appear in load_all_reports."""
    (tmp_path / "branchA__verify.md").write_text(
        "# Report A\n\n## Verdict\n\nPASS.", encoding="utf-8"
    )
    (tmp_path / "branchB__verify.md").write_text(
        "# Report B\n\n## Verdict\n\nFAIL.", encoding="utf-8"
    )
    views = load_all_reports(tmp_path)
    assert len(views) == 2, "both verify reports must be returned, none silently dropped"
    titles = {v.title for v in views}
    assert "Report A" in titles
    assert "Report B" in titles


def test_load_all_reports_each_file_parsed_independently(tmp_path: Path) -> None:
    """load_all_reports parses each file on its own merits (no dedup by role)."""
    (tmp_path / "x__executor.md").write_text(
        "# Exec X\n\n## Verdict\n\nPASS.", encoding="utf-8"
    )
    (tmp_path / "y__executor.md").write_text(
        "# Exec Y\n\n## Verdict\n\nFAIL.", encoding="utf-8"
    )
    views = load_all_reports(tmp_path)
    verdicts = {v.verdict for v in views}
    assert "pass" in verdicts and "fail" in verdicts, (
        "each file must be parsed independently; both verdicts must be present"
    )
