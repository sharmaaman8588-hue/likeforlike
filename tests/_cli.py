"""Normalise CLI output so assertions hold under any terminal width or colour setting.

CI runners make Typer force a Rich terminal: help and tables then carry ANSI escapes and wrap
at 80 columns, which splits a phrase across lines. Assert against `plain(result)` instead.
"""

from __future__ import annotations

import re

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def plain(result) -> str:
    """The output minus ANSI escapes, with each whitespace run collapsed to one space."""
    return " ".join(strip_ansi(result.output).split())


def output_lines(result) -> list[str]:
    """The command output split into lines, ANSI escapes removed, for row-oriented table checks."""
    return strip_ansi(result.output).splitlines()
