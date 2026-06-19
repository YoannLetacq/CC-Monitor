"""Pure slug/path resolution helpers for Claude project discovery.

Claude stores transcripts under ``~/.claude/projects/<slug>/`` where the
slug is derived from the absolute project path by replacing every path
separator with a dash. That transformation is lossy (a real directory name
may itself contain a dash), so the reverse is best-effort only. When a
session's ``cwd`` is known (from ``session-started.json``) it is treated as
authoritative; otherwise the reverse path is validated against the disk.
"""

from pathlib import Path


def slug_to_path(slug: str) -> Path:
    """Reverse a project slug to a best-effort absolute path.

    Each dash is mapped back to a path separator and a leading ``~`` is
    expanded. Existence is *not* checked: the function stays pure.
    """
    expanded: str = slug.replace("-", "/")
    return Path(expanded).expanduser()


def path_to_slug(path: Path) -> str:
    """Convert an absolute project path to its canonical slug form."""
    absolute: Path = path.expanduser().resolve() if not path.is_absolute() else path
    return str(absolute).replace("/", "-")


def resolve_project(slug: str, cwd: str | None) -> tuple[Path, bool]:
    """Resolve a project path from a slug, preferring an authoritative cwd.

    When ``cwd`` is provided it is returned with ``resolved=True``. Otherwise
    the slug is reversed best-effort and ``resolved`` reflects whether that
    reverse path exists on disk.
    """
    if cwd is not None:
        return Path(cwd), True
    candidate: Path = slug_to_path(slug)
    return candidate, candidate.exists()
