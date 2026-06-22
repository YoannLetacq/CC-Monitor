"""Tests for the incremental tail service (tailer.py).

TDD sequence — 7 cases from INCR3_DISPATCH §3:
1. Empty file yields no lines, offset 0.
2. Three complete lines yield 3 dicts; re-call yields nothing (stable offset).
3. Appending a 4th line yields only the new line.
4. Partial line (no trailing newline) is not consumed; once completed, returned once.
5. Corrupt JSON line is skipped; surrounding valid lines pass through.
6. open_tail on file larger than max_bytes starts after the first newline post-seek.
7. Truncation (file shrinks below offset) restarts from 0.
"""

import json
from pathlib import Path

import pytest

from app.services.tailer import TailState, open_tail, read_new_lines


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_lines(path: Path, lines: list[str]) -> None:
    """Write lines to *path*, each terminated by a newline."""
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def _append_line(path: Path, line: str, *, complete: bool = True) -> None:
    """Append *line* to *path*, optionally without the trailing newline."""
    suffix = "\n" if complete else ""
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{line}{suffix}")


def _json(payload: dict) -> str:
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# Case 1 — empty file
# ---------------------------------------------------------------------------


class TestEmptyFile:
    """Case 1: empty file yields no lines, offset stays 0."""

    def test_empty_file_returns_no_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.jsonl"
        p.write_text("", encoding="utf-8")
        state = open_tail(p, max_bytes=1_000_000)
        lines, new_state = read_new_lines(state)
        assert lines == []
        assert new_state.offset == 0

    def test_missing_file_offset_zero(self, tmp_path: Path) -> None:
        p = tmp_path / "missing.jsonl"
        state = open_tail(p, max_bytes=1_000_000)
        assert state.offset == 0
        lines, new_state = read_new_lines(state)
        assert lines == []
        assert new_state.offset == 0


# ---------------------------------------------------------------------------
# Case 2 — 3 complete lines; re-call stable
# ---------------------------------------------------------------------------


class TestThreeLinesStable:
    """Case 2: 3 complete lines yield 3 dicts; re-call yields nothing."""

    def test_three_lines_parsed(self, tmp_path: Path) -> None:
        p = tmp_path / "three.jsonl"
        payloads = [{"a": 1}, {"b": 2}, {"c": 3}]
        _write_lines(p, [_json(pl) for pl in payloads])

        state = open_tail(p, max_bytes=1_000_000)
        lines, _ = read_new_lines(state)
        assert lines == payloads

    def test_recall_yields_nothing(self, tmp_path: Path) -> None:
        p = tmp_path / "three.jsonl"
        payloads = [{"a": 1}, {"b": 2}, {"c": 3}]
        _write_lines(p, [_json(pl) for pl in payloads])

        state = open_tail(p, max_bytes=1_000_000)
        _, state2 = read_new_lines(state)
        lines2, state3 = read_new_lines(state2)
        assert lines2 == []
        assert state3.offset == state2.offset


# ---------------------------------------------------------------------------
# Case 3 — append of a 4th line
# ---------------------------------------------------------------------------


class TestAppendNewLine:
    """Case 3: appending a 4th line yields only the new line."""

    def test_only_new_line_returned(self, tmp_path: Path) -> None:
        p = tmp_path / "append.jsonl"
        payloads = [{"a": 1}, {"b": 2}, {"c": 3}]
        _write_lines(p, [_json(pl) for pl in payloads])

        state = open_tail(p, max_bytes=1_000_000)
        _, state2 = read_new_lines(state)

        new_payload = {"d": 4}
        _append_line(p, _json(new_payload))

        lines, _ = read_new_lines(state2)
        assert lines == [new_payload]


# ---------------------------------------------------------------------------
# Case 4 — partial line not consumed, then completed
# ---------------------------------------------------------------------------


class TestPartialLine:
    """Case 4: partial line is not consumed; completed line is returned once."""

    def test_partial_not_consumed(self, tmp_path: Path) -> None:
        p = tmp_path / "partial.jsonl"
        p.write_text("", encoding="utf-8")
        state = open_tail(p, max_bytes=1_000_000)

        _append_line(p, _json({"partial": True}), complete=False)
        lines, state2 = read_new_lines(state)
        assert lines == []
        assert state2.offset == 0

    def test_completed_line_returned_once(self, tmp_path: Path) -> None:
        p = tmp_path / "partial.jsonl"
        p.write_text("", encoding="utf-8")
        state = open_tail(p, max_bytes=1_000_000)

        payload = {"partial": True}
        _append_line(p, _json(payload), complete=False)
        _, state2 = read_new_lines(state)

        # Now complete the line
        with p.open("a", encoding="utf-8") as fh:
            fh.write("\n")
        lines, state3 = read_new_lines(state2)
        assert lines == [payload]
        assert state3.offset > state2.offset


# ---------------------------------------------------------------------------
# Case 5 — corrupt JSON line skipped
# ---------------------------------------------------------------------------


class TestCorruptLineskipped:
    """Case 5: corrupt JSON line skipped; surrounding valid lines pass."""

    def test_corrupt_line_skipped(self, tmp_path: Path) -> None:
        p = tmp_path / "corrupt.jsonl"
        lines_raw = [
            _json({"first": 1}),
            "NOT_VALID_JSON{{{",
            _json({"third": 3}),
        ]
        _write_lines(p, lines_raw)

        state = open_tail(p, max_bytes=1_000_000)
        lines, _ = read_new_lines(state)
        assert lines == [{"first": 1}, {"third": 3}]


# ---------------------------------------------------------------------------
# Case 6 — open_tail with file larger than max_bytes
# ---------------------------------------------------------------------------


class TestOpenTailMaxBytes:
    """Case 6: open_tail on file > max_bytes starts after first newline post-seek."""

    def test_starts_after_first_newline(self, tmp_path: Path) -> None:
        p = tmp_path / "big.jsonl"
        # Three lines; we'll set max_bytes smaller than the full file
        line1 = _json({"line": 1})
        line2 = _json({"line": 2})
        line3 = _json({"line": 3})
        content = f"{line1}\n{line2}\n{line3}\n"
        p.write_text(content, encoding="utf-8")

        # max_bytes set so the seek lands inside line2 but before its newline,
        # meaning the tailer must skip to the next complete line (line3).
        size = p.stat().st_size
        # seek into line2 territory
        max_bytes = size - len(line3) - 2  # lands roughly in line2
        state = open_tail(p, max_bytes=max_bytes)

        lines, _ = read_new_lines(state)
        # Must not start with a partial line; line3 must be present
        assert {"line": 3} in lines
        # line1 must NOT be present (it was before the seek point)
        assert {"line": 1} not in lines

    def test_small_file_starts_at_zero(self, tmp_path: Path) -> None:
        p = tmp_path / "small.jsonl"
        _write_lines(p, [_json({"x": 1})])
        state = open_tail(p, max_bytes=1_000_000)
        assert state.offset == 0


# ---------------------------------------------------------------------------
# Case 7 — truncation restarts from 0
# ---------------------------------------------------------------------------


class TestTruncation:
    """Case 7: truncation (file smaller than offset) restarts from 0."""

    def test_truncation_restarts(self, tmp_path: Path) -> None:
        p = tmp_path / "trunc.jsonl"
        _write_lines(p, [_json({"a": i}) for i in range(5)])

        state = open_tail(p, max_bytes=1_000_000)
        _, state2 = read_new_lines(state)
        assert state2.offset > 0

        # Truncate: write a shorter file
        p.write_text(_json({"restarted": True}) + "\n", encoding="utf-8")

        lines, state3 = read_new_lines(state2)
        assert state3.offset > 0
        assert {"restarted": True} in lines
