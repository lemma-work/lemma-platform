#!/usr/bin/env python3
"""Stamp x-lemma metadata onto the committed OpenAPI spec (Wave 3, CG-4).

The live backend injects x-lemma in `custom_openapi()`. This script applies the *same*
`apply_lemma_metadata` function to the committed `lemma-python/lemma_sdk/openapi_spec.json`
so the committed artifact equals what a fresh regen would produce — without needing a
running backend. It re-emits with the exact formatting the spec pipeline uses
(`indent=2, sort_keys=True`, trailing newline) so there are no spurious diffs.

Usage:
    python scripts/stamp_lemma_metadata.py [--check]

`--check` exits non-zero if the spec is missing/incomplete x-lemma or would change,
for use as a CI gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lemma-backend"))

from app.core.openapi_extensions import apply_lemma_metadata, validate_metadata_coverage  # noqa: E402

SPEC_PATH = ROOT / "lemma-python" / "lemma_sdk" / "openapi_spec.json"


def _render(spec: dict) -> str:
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the spec would change or is incomplete.")
    args = parser.parse_args()

    original = SPEC_PATH.read_text(encoding="utf-8")
    spec = json.loads(original)
    apply_lemma_metadata(spec)

    problems = validate_metadata_coverage(spec)
    if problems:
        print(f"x-lemma metadata incomplete ({len(problems)} problem(s)):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    rendered = _render(spec)
    annotated = sum(1 for _ in _iter_x_lemma(spec))

    if args.check:
        if rendered != original:
            print("Committed openapi_spec.json is stale — run: python scripts/stamp_lemma_metadata.py", file=sys.stderr)
            return 1
        print(f"x-lemma metadata up to date ({annotated} operations annotated).")
        return 0

    SPEC_PATH.write_text(rendered, encoding="utf-8")
    print(f"Stamped x-lemma metadata onto {annotated} operations in {SPEC_PATH.relative_to(ROOT)}.")
    return 0


def _iter_x_lemma(spec: dict):
    for item in spec.get("paths", {}).values():
        if not isinstance(item, dict):
            continue
        for operation in item.values():
            if isinstance(operation, dict) and "x-lemma" in operation:
                yield operation


if __name__ == "__main__":
    raise SystemExit(main())
