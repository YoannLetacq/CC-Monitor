"""Pydantic schemas for the read-only tmux data source (pole backend-tmux).

Wire-format models for ``GET /api/tmux/sessions`` (nested session → window →
pane tree) and the ``GET /api/tmux/panes/{id}/events`` SSE stream. As
everywhere in this API, JSON keys are camelCase (``alias_generator=to_camel``)
while Python code uses snake_case (``claude_session_id`` → ``claudeSessionId``).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class TmuxPane(BaseModel):
    """A single tmux pane as reported by ``list-panes -a``.

    ``id`` is the tmux pane id **without** the ``%`` prefix (e.g. ``"3"``).
    ``claude_session_id`` is the best-effort correlated Claude Code session
    id; ``None`` is normal and never blocks discovery.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: str
    index: int
    title: str
    active: bool
    width: int
    height: int
    command: str
    claude_session_id: str | None = None


class TmuxWindow(BaseModel):
    """A tmux window and its panes."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    index: int
    name: str
    panes: list[TmuxPane]


class TmuxSession(BaseModel):
    """A tmux session and its windows."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    name: str
    windows: list[TmuxWindow]


class PaneSnapshotEvent(BaseModel):
    """SSE ``snapshot`` payload: the pane's full visible content at connect.

    ``cursor`` is the line count of the snapshot (one past the last line),
    which is where an autoscrolling client places its viewport.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    pane_id: str
    lines: list[str]
    cursor: int


class PaneDeltaEvent(BaseModel):
    """SSE ``delta`` payload: the FULL current screen after a change.

    A terminal is screen-oriented, so deltas use replace semantics — the
    client repaints from ``lines`` rather than appending.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    pane_id: str
    lines: list[str]


class PaneHeartbeatEvent(BaseModel):
    """SSE ``heartbeat`` payload emitted when the pane content is unchanged."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    pane_id: str
    ts: datetime


class PaneClosedEvent(BaseModel):
    """SSE ``pane_closed`` payload: the pane no longer exists; stream ends."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    pane_id: str
