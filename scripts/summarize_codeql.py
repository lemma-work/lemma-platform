#!/usr/bin/env python3
"""Turn a CodeQL SARIF file into something worth reading in a terminal.

Prints `path:line  rule  message`, grouped by severity, and exits non-zero when
anything is reported -- so `make codeql` fails the way CI would rather than
printing a wall of JSON and succeeding.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# SARIF calls these "levels"; CodeQL maps its own severities onto them.
ORDER = ("error", "warning", "note")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sarif", required=True, type=Path)
    parser.add_argument("--changed-files", required=True, type=Path)
    parser.add_argument("--scope", choices=("diff", "all"), default="diff")
    parser.add_argument("--allow", type=Path, default=Path(".codeql-allow.txt"))
    return parser.parse_args()


def load_allowlist(path: Path) -> list[tuple[str, str]]:
    """Accepted (rule, path-prefix) pairs, so the gate stays actionable.

    A checker nobody can get to zero is a checker everybody learns to ignore,
    so a finding that has been read and judged fine is recorded here with its
    reason rather than left to fail every run.
    """
    if not path.is_file():
        return []
    allowed: list[tuple[str, str]] = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        allowed.append((parts[0], parts[1].strip()))
    return allowed


def rule_levels(run: dict) -> dict[str, str]:
    """Default level per rule, since a result may omit its own."""
    levels: dict[str, str] = {}
    driver = run.get("tool", {}).get("driver", {})
    for rule in driver.get("rules", []) or []:
        rule_id = rule.get("id")
        if not rule_id:
            continue
        level = (rule.get("defaultConfiguration") or {}).get("level")
        levels[rule_id] = level or "warning"
    return levels


def main() -> int:
    args = parse_args()
    if not args.sarif.is_file():
        print(f"no SARIF at {args.sarif}", file=sys.stderr)
        return 2

    changed: set[str] = set()
    if args.scope == "diff" and args.changed_files.is_file():
        changed = {
            line.strip()
            for line in args.changed_files.read_text().splitlines()
            if line.strip()
        }

    allowed = load_allowlist(args.allow)
    sarif = json.loads(args.sarif.read_text())
    findings: dict[str, list[tuple[str, int, str, str]]] = defaultdict(list)
    accepted = 0

    for run in sarif.get("runs", []) or []:
        levels = rule_levels(run)
        for result in run.get("results", []) or []:
            rule_id = result.get("ruleId") or "?"
            level = result.get("level") or levels.get(rule_id, "warning")
            message = (result.get("message") or {}).get("text", "").strip()
            for location in result.get("locations", []) or []:
                physical = location.get("physicalLocation") or {}
                uri = (physical.get("artifactLocation") or {}).get("uri", "")
                line = (physical.get("region") or {}).get("startLine", 0)
                if args.scope == "diff" and uri not in changed:
                    continue
                if any(
                    rule_id == rule and uri.startswith(prefix)
                    for rule, prefix in allowed
                ):
                    accepted += 1
                    continue
                findings[level].append((uri, line, rule_id, message))

    total = sum(len(v) for v in findings.values())
    suffix = f" ({accepted} accepted via {args.allow})" if accepted else ""
    if total == 0:
        scope = "in changed files" if args.scope == "diff" else "repository-wide"
        print(f"CodeQL: no findings {scope}{suffix}.")
        return 0

    for level in ORDER:
        entries = sorted(set(findings.get(level, [])))
        if not entries:
            continue
        print(f"\n{level.upper()} ({len(entries)})")
        for uri, line, rule_id, message in entries:
            print(f"  {uri}:{line}  {rule_id}")
            print(f"      {message}")

    print(f"\nCodeQL: {total} finding(s){suffix}.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
