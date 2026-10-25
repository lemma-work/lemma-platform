"""Telling a caller that a flag or field they used is on its way out.

Pod connectors replaced addressing a connector by installation and account.
The old flags still work -- live scripts, agents and pods call them -- but they
are hidden from help and say, in one line on stderr, what to use instead. The
server says the same of the API call in an ``X-Lemma-Deprecation`` header,
which the SDK raises as a ``LemmaDeprecationWarning``; ``show_sdk_deprecations``
prints that as one line too, instead of Python's two-line warning with a
source excerpt from inside the SDK.
"""

from __future__ import annotations

import sys
import warnings

import typer

#: Matched by name, so installing the formatter costs no SDK import at startup.
_SDK_DEPRECATION = "LemmaDeprecationWarning"


def warn_deprecated(use: str, instead: str) -> None:
    """One line on stderr: what was used, and what to use instead."""
    typer.echo(f"warning: {use} is deprecated: {instead}", err=True)


def show_sdk_deprecations() -> None:
    """Print the SDK's deprecation warnings as one ``warning:`` line each.

    Nothing is filtered: every other warning keeps Python's own formatting, and
    the SDK already warns once per message per process.
    """
    previous = warnings.showwarning

    def show(message, category, filename, lineno, file=None, line=None):  # type: ignore[no-untyped-def]
        if any(cls.__name__ == _SDK_DEPRECATION for cls in category.__mro__):
            (file or sys.stderr).write(f"warning: {message}\n")
            return
        previous(message, category, filename, lineno, file, line)

    warnings.showwarning = show
