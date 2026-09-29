#!/usr/bin/env python3
"""Which lines a change touched, and which CodeQL findings have been accepted.

Shared by `make codeql` (through run_codeql.sh and summarize_codeql.py) and the
pull-request comment in `.github/workflows/security.yml`, so the terminal and
the PR scope a finding to "this change" by the same rule.

The rule is line ranges, not filenames. A file-level scope reports every
pre-existing finding in any file you touched: renaming one import in a
600-line module made it look like the module had sprouted seven new problems.

Run as a filter, it turns `git diff -U0` into the `path:start-end` lines that
summarize_codeql.py reads:

    git diff -U0 origin/main...HEAD | python3 scripts/codeql_scope.py

Standard library only, and no syntax newer than 3.9: CI runs it with the
runner's system Python, and run_codeql.sh with whatever `python3` is on PATH.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

LineRange = Tuple[int, int]
ChangedLines = Dict[str, List[LineRange]]

_HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")


def parse_unified_diff(lines: Iterable[str]) -> ChangedLines:
    """Map each file in a `git diff -U0` to the new-side line ranges it added.

    A hunk that only deletes has a new-side length of zero and contributes no
    range: there is no line left in the file for a finding to sit on.
    """
    changed: ChangedLines = {}
    current = ""
    for raw in lines:
        line = raw.rstrip("\n")
        if line.startswith("+++ "):
            target = line[4:]
            current = target[2:] if target.startswith("b/") else ""
            continue
        match = _HUNK.match(line)
        if not match or not current:
            continue
        start = int(match.group(1))
        length = 1 if match.group(2) is None else int(match.group(2))
        if length > 0:
            changed.setdefault(current, []).append((start, start + length - 1))
    return changed


def format_ranges(changed: ChangedLines) -> List[str]:
    return [
        f"{path}:{start}-{end}"
        for path, spans in changed.items()
        for start, end in spans
    ]


def read_ranges(path: Path) -> ChangedLines:
    """Read back what `format_ranges` wrote. Malformed lines are skipped."""
    changed: ChangedLines = {}
    if not path.is_file():
        return changed
    for entry in path.read_text().splitlines():
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        file_path, _, span = entry.rpartition(":")
        start, _, end = span.partition("-")
        try:
            changed.setdefault(file_path, []).append((int(start), int(end)))
        except ValueError:
            continue
    return changed


def touched(changed: ChangedLines, path: str, line: int) -> bool:
    return any(start <= line <= end for start, end in changed.get(path, ()))


def load_allowlist(path: Path) -> List[Tuple[str, str]]:
    """Accepted (rule, path-prefix) pairs from `.codeql-allow.txt`.

    A checker nobody can get to zero is a checker everybody learns to ignore,
    so a finding that has been read and judged fine is recorded there with its
    reason rather than left to fail every run.
    """
    if not path.is_file():
        return []
    allowed: List[Tuple[str, str]] = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        allowed.append((parts[0], parts[1].strip()))
    return allowed


def is_allowed(allowed: List[Tuple[str, str]], rule_id: str, path: str) -> bool:
    return any(rule_id == rule and path.startswith(prefix) for rule, prefix in allowed)


def main() -> int:
    for entry in format_ranges(parse_unified_diff(sys.stdin)):
        print(entry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
