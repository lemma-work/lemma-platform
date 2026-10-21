"""One parse of each source file per test process, shared by the source gates.

A few dozen unit tests are static gates: they walk the tree and parse every
file to hold a rule over the code -- the logging contract, tool authority, the
transports that must stay gone. Each used to read and parse the files for
itself, so the lane parsed the same few thousand files over and over, which
was seconds of every run apiece.

Files do not change during a test session, so each is read and parsed once and
the result handed to every gate that asks. Callers must treat a returned tree
as read-only: it is the same object the next gate walks.

The cache is per process. Under xdist each worker builds its own, which is
still one parse per worker rather than one per gate.
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path


@cache
def _python_files(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(root.rglob("*.py")))


def python_files(root: Path) -> tuple[Path, ...]:
    """Every ``*.py`` under ``root``, sorted -- the same as ``sorted(rglob)``.

    The paths are spelled from ``root`` as given, exactly as ``rglob`` would
    spell them, so a caller's ``relative_to`` keeps working.
    """
    if root.is_absolute() and root == root.resolve():
        return _python_files(root)
    resolved = root.resolve()
    return tuple(root / path.relative_to(resolved) for path in _python_files(resolved))


@cache
def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def source(path: Path) -> str:
    """The text of ``path``, read once per process."""
    return _source(path.resolve())


@cache
def _tree(path: Path) -> ast.Module:
    return ast.parse(_source(path), filename=str(path))


def tree(path: Path) -> ast.Module:
    """The parsed module at ``path``, parsed once per process. Do not mutate."""
    return _tree(path.resolve())
