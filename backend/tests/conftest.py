"""Shared pytest fixtures building an anonymized Claude-projects tree.

These fixtures reproduce the real on-disk layout described in the dispatch
(transcripts under ``<projects_root>/<slug>/<sid>.jsonl`` and per-project
state under ``<project>/.omc/state/sessions/<sid>/session-started.json``)
inside a temporary directory. The real ``~/.claude`` is never read.
"""

import json
from pathlib import Path
from typing import Callable

import pytest


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    """Write one JSON object per line to ``path`` (test helper, write mode)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line))
            handle.write("\n")


def _write_session_started(project_root: Path, payload: dict) -> None:
    """Write a ``session-started.json`` file for the given project root."""
    sid = payload["session_id"]
    state_dir = project_root / ".omc" / "state" / "sessions" / sid
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / "session-started.json", "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


@pytest.fixture()
def make_transcript(tmp_path: Path) -> Callable[[str, str, list[dict]], Path]:
    """Return a factory creating ``<projects_root>/<slug>/<sid>.jsonl``."""
    projects_root = tmp_path / "claude_projects"

    def _factory(slug: str, sid: str, lines: list[dict]) -> Path:
        transcript = projects_root / slug / f"{sid}.jsonl"
        _write_jsonl(transcript, lines)
        return transcript

    return _factory


@pytest.fixture()
def projects_root(tmp_path: Path) -> Path:
    """Return the anonymized projects root used by transcript fixtures."""
    root = tmp_path / "claude_projects"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture()
def write_state() -> Callable[[Path, dict], None]:
    """Return a helper writing a ``session-started.json`` payload."""
    return _write_session_started


@pytest.fixture()
def make_project(
    tmp_path: Path, projects_root: Path
) -> Callable[..., Path]:
    """Return a factory creating a coherent project + transcript + state.

    The project lives under ``<tmp>/work/<name>`` and its slug is derived by
    the same dash transform Claude uses, so the slug reverses back to the
    real directory (matching D6). The transcript is written under
    ``<projects_root>/<slug>/<sid>.jsonl`` and, when ``with_state`` is set,
    a ``session-started.json`` is written under the project directory.
    """
    work_root = tmp_path / "work"

    def _factory(
        name: str,
        sid: str,
        lines: list[dict],
        with_transcript: bool = True,
        with_state: bool = True,
        extra_state_sids: tuple[str, ...] = (),
    ) -> Path:
        project_dir = work_root / name
        project_dir.mkdir(parents=True, exist_ok=True)
        slug = str(project_dir).replace("/", "-")
        if with_transcript:
            stamped = [
                {**line, "cwd": str(project_dir)}
                if "timestamp" in line
                else line
                for line in lines
            ]
            _write_jsonl(projects_root / slug / f"{sid}.jsonl", stamped)
        else:
            (projects_root / slug).mkdir(parents=True, exist_ok=True)
        if with_state:
            _write_session_started(
                project_dir,
                {
                    "session_id": sid,
                    "started_at": "2026-06-19T08:00:00Z",
                    "cwd": str(project_dir),
                    "pid": 1234,
                    "boot_id": "boot",
                },
            )
        for extra in extra_state_sids:
            _write_session_started(
                project_dir,
                {
                    "session_id": extra,
                    "started_at": "2026-06-19T07:00:00Z",
                    "cwd": str(project_dir),
                    "pid": 4321,
                    "boot_id": "boot",
                },
            )
        return project_dir

    return _factory
