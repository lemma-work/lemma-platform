#!/usr/bin/env python3
"""Enforce total and risk-adjusted module coverage floors."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def _module(filename: str) -> str:
    parts = filename.replace("\\", "/").split("app/", 1)[-1].split("/")
    if len(parts) >= 2 and parts[0] == "modules":
        return parts[1]
    return parts[0]


def _skip(filename: str) -> bool:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    return "/tests/" in filename or name.startswith("test_") or name == "conftest.py"


def _percentage(covered: int, statements: int) -> float:
    return 100.0 if statements == 0 else covered / statements * 100


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("coverage_json", type=Path)
    parser.add_argument("--min-total", type=float, default=70.0)
    parser.add_argument("--min-module", action="append", default=[])
    args = parser.parse_args()

    report: dict[str, Any] = json.loads(args.coverage_json.read_text(encoding="utf-8"))
    modules: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    total_statements = 0
    total_covered = 0
    for filename, metadata in report.get("files", {}).items():
        if _skip(filename):
            continue
        summary = metadata["summary"]
        statements = int(summary["num_statements"])
        covered = statements - int(summary["missing_lines"])
        total_statements += statements
        total_covered += covered
        values = modules[_module(filename)]
        values[0] += statements
        values[1] += covered

    failures: list[str] = []
    total = _percentage(total_covered, total_statements)
    if total < args.min_total:
        failures.append(f"total coverage {total:.2f}% is below {args.min_total:.2f}%")
    module_floors = args.min_module or ["schedule=65"]
    for specification in module_floors:
        name, raw_floor = specification.split("=", 1)
        floor = float(raw_floor)
        statements, covered = modules[name]
        percentage = _percentage(covered, statements)
        if percentage < floor:
            failures.append(
                f"{name} coverage {percentage:.2f}% is below {floor:.2f}%"
            )

    if failures:
        print("Coverage gate failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(f"Coverage gate passed: total {total:.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
