"""Incremental tail of append-only JSONL files by byte offset.

Provides bounded, offset-tracked reading of JSONL transcript files without
re-reading from the start on every poll. Handles truncation/rotation, partial
lines in flight, and corrupt JSON defensively (skip + debug log).

Public API
----------
- ``TailState`` — dataclass holding the file path and current byte offset.
- ``open_tail(path, max_bytes) -> TailState`` — compute the initial offset,
  capped so at most ``max_bytes`` of history are replayed.
- ``read_new_lines(state) -> tuple[list[dict], TailState]`` — consume all
  complete newline-terminated lines since the last call and advance the offset.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TailState:
    """Mutable tracking state for one tailed file.

    Attributes
    ----------
    path:
        Absolute path to the JSONL file being tailed.
    offset:
        Byte position of the next unread byte in the file.
        Updated after each successful ``read_new_lines`` call.
    """

    path: Path
    offset: int


def open_tail(path: Path, max_bytes: int) -> TailState:
    """Compute the initial ``TailState`` for *path*, bounded by *max_bytes*.

    If the file is absent or empty the offset is set to 0.  If the file is
    larger than *max_bytes* the read head is placed at ``size - max_bytes``
    and then advanced past the first newline so that the first call to
    ``read_new_lines`` never returns a partial (split) JSON line.

    Parameters
    ----------
    path:
        Path to the JSONL file to tail.
    max_bytes:
        Maximum number of bytes of history to replay on first read.
        Must be a positive integer.

    Returns
    -------
    TailState
        Initial state with the computed byte offset.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        logger.debug("open_tail: cannot stat %s, starting at 0: %s", path, exc)
        return TailState(path=path, offset=0)

    if size <= max_bytes:
        return TailState(path=path, offset=0)

    seek_to = size - max_bytes
    offset = _skip_to_next_line(path, seek_to)
    return TailState(path=path, offset=offset)


def _skip_to_next_line(path: Path, seek_to: int) -> int:
    """Seek to *seek_to* then advance past the next newline character.

    Returns the byte position immediately after the first ``\\n`` found at or
    after *seek_to*, or *seek_to* itself when no newline is found (edge case
    for a file with a single very long line).

    Parameters
    ----------
    path:
        File to seek in (opened read-only).
    seek_to:
        Initial seek position (bytes from start of file).

    Returns
    -------
    int
        Byte offset just past the first newline, safe for use as a
        ``TailState.offset``.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            handle.seek(seek_to)
            line = handle.readline()
            if line:
                return handle.tell()
            return seek_to
    except OSError as exc:
        logger.debug("open_tail: cannot seek in %s: %s", path, exc)
        return 0


def read_new_lines(state: TailState) -> tuple[list[dict], TailState]:
    """Read all complete lines appended since the last call.

    Opens the file in read-only text mode, seeks to ``state.offset``, then
    consumes every line that is terminated by a newline character.  Lines
    without a trailing newline (write in progress) are left for the next call.
    Each complete line is decoded as JSON; corrupt lines are skipped with a
    debug log entry.

    Truncation guard: if the current file size is smaller than ``state.offset``
    the file has been rotated or rewritten; the offset resets to 0 and the
    entire new content is read.

    Parameters
    ----------
    state:
        Current tail position for the file.

    Returns
    -------
    tuple[list[dict], TailState]
        A list of successfully parsed JSON objects and the updated
        ``TailState`` whose offset points past the last consumed line.
    """
    try:
        size = state.path.stat().st_size
    except OSError as exc:
        logger.debug("read_new_lines: cannot stat %s: %s", state.path, exc)
        return [], state

    # Truncation: file is shorter than our remembered offset → restart.
    effective_offset = 0 if size < state.offset else state.offset

    records, new_offset = _drain_complete_lines(state.path, effective_offset)
    return records, TailState(path=state.path, offset=new_offset)


def _drain_complete_lines(path: Path, offset: int) -> tuple[list[dict], int]:
    """Read complete newline-terminated lines from *path* starting at *offset*.

    Only lines that end with ``\\n`` are consumed.  A line without a trailing
    newline is left in place (its start position is not included in the
    returned offset).

    Parameters
    ----------
    path:
        File to read (opened read-only).
    offset:
        Byte position to seek to before reading.

    Returns
    -------
    tuple[list[dict], int]
        Parsed records and the new byte offset (past the last consumed
        newline).
    """
    records: list[dict] = []
    current_offset = offset

    try:
        with open(path, "r", encoding="utf-8") as handle:
            handle.seek(offset)
            while True:
                line = handle.readline()
                if not line:
                    break
                if not line.endswith("\n"):
                    # Partial line (write in progress) — do not advance offset.
                    break
                current_offset = handle.tell()
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    records.append(json.loads(stripped))
                except json.JSONDecodeError as exc:
                    logger.debug(
                        "read_new_lines: skipping corrupt line in %s: %s",
                        path,
                        exc,
                    )
    except OSError as exc:
        logger.debug("read_new_lines: cannot read %s: %s", path, exc)

    return records, current_offset
