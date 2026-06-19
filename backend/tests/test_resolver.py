"""Unit tests for the pure slug/path resolver service."""

from pathlib import Path

from app.services.resolver import path_to_slug, resolve_project, slug_to_path


def test_path_to_slug_is_reverse_of_slug_to_path_simple() -> None:
    """A path without dashes round-trips through slug and back."""
    path = Path("/home/user/myproject")
    slug = path_to_slug(path)
    assert slug == "-home-user-myproject"
    assert slug_to_path(slug) == path


def test_slug_to_path_does_not_check_existence() -> None:
    """``slug_to_path`` is pure and returns a path even if it does not exist."""
    result = slug_to_path("-does-not-exist-anywhere")
    assert result == Path("/does/not/exist/anywhere")


def test_resolve_project_uses_cwd_when_present() -> None:
    """When ``cwd`` is given it is authoritative and ``resolved`` is True."""
    resolved_path, resolved = resolve_project("-some-slug", "/real/project/path")
    assert resolved_path == Path("/real/project/path")
    assert resolved is True


def test_resolve_project_lossy_slug_marks_unresolved() -> None:
    """Without ``cwd`` and a non-existent reverse path, ``resolved`` is False."""
    resolved_path, resolved = resolve_project("-does-not-exist-anywhere-xyz", None)
    assert resolved_path == Path("/does/not/exist/anywhere/xyz")
    assert resolved is False


def test_resolve_project_existing_reverse_path_marks_resolved() -> None:
    """Without ``cwd`` but with an existing reverse path, ``resolved`` is True.

    Uses a real dash-free system directory so the round-trip is lossless.
    """
    resolved_path, resolved = resolve_project("-usr", None)
    assert resolved_path == Path("/usr")
    assert resolved is True


def test_lossy_slug_reverse_is_not_round_trip() -> None:
    """A path segment containing a dash does not survive the round trip (D2)."""
    original = Path("/home/yoann/ERP_TREUIL")
    slug = "-home-yoann-ERP-TREUIL"
    assert slug_to_path(slug) != original
