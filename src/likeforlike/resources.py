"""Locate files that ship with the project (SQL, policy, templates)."""

from __future__ import annotations

from pathlib import Path

_PACKAGE = Path(__file__).resolve().parent
_REPO_ROOT = _PACKAGE.parents[1]


def project_file(relative: str) -> Path:
    """A file from the repository root, or from the installed package when installed."""
    for base in (_REPO_ROOT, _PACKAGE):
        candidate = base / relative
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"project file not found: {relative}")


def template_dir() -> Path:
    return _PACKAGE / "templates"
