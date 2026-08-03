#!/usr/bin/env python3
"""Every Lemma-owned component must name the same version — checked at release.

Versions used to be forced apart branch by branch: regeneration refused a schema
change that did not also bump ``API_VERSION``, so a number naming a release that
had not happened climbed once per PR, and every schema-touching branch conflicted
with every other on the same handful of lines. Rebasing produced a version that
meant nothing.

Consistency still matters — a CLI built against one spec talking to a server
built from another is exactly the skew the version string exists to surface — but
it matters at the moment something is *published*, not at the moment it is
written. So the check lives here, and the release workflows run it against the
tag before publishing anything.

Usage::

    python scripts/check_version_consistency.py                 # all components agree
    python scripts/check_version_consistency.py --expect 0.7.0  # ...and match the tag
    python scripts/check_version_consistency.py --expect v0.7.0 # leading v is fine

Exits non-zero, listing every disagreement, when the components do not line up.
Add a component here the moment it carries a version people can install.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-.][0-9A-Za-z.-]+)?$")


@dataclass(frozen=True)
class Source:
    """One declared version: where it lives and how to read it out."""

    label: str
    path: Path
    pattern: re.Pattern[str] | None = None
    #: Keys to walk for JSON files, e.g. ("info", "version"). Structural rather
    #: than a regex because the spec is emitted with sorted keys, where any
    #: schema property named "version" could sort ahead of the real one.
    json_path: tuple[str, ...] = ("version",)

    def read(self) -> str | None:
        """The declared version, or None when the file or the field is absent.

        A missing file is reported rather than raising: this runs in release
        workflows, where a checkout that lost a component should fail loudly with
        the rest of the report instead of a traceback on the first one.
        """
        if not self.path.exists():
            return None
        text = self.path.read_text(encoding="utf-8")
        if self.pattern is not None:
            match = self.pattern.search(text)
            return match.group(1) if match else None
        node: object = json.loads(text)
        for key in self.json_path:
            if not isinstance(node, dict) or key not in node:
                return None
            node = node[key]
        return str(node) or None


# The version *is* the artifact's identity for each of these: the API it serves,
# the package a user installs, or the spec a generated client was built against.
SOURCES: tuple[Source, ...] = (
    Source(
        "lemma-backend API_VERSION",
        REPO_ROOT / "lemma-backend/app/version.py",
        re.compile(r'(?m)^API_VERSION = "([^"]+)"'),
    ),
    Source(
        "lemma-python package",
        REPO_ROOT / "lemma-python/pyproject.toml",
        re.compile(r'(?m)^version = "([^"]+)"'),
    ),
    Source(
        "lemma-python _spec_info.API_VERSION",
        REPO_ROOT / "lemma-python/lemma_sdk/_spec_info.py",
        re.compile(r'(?m)^API_VERSION = "([^"]+)"'),
    ),
    Source(
        "lemma-python bundled spec info.version",
        REPO_ROOT / "lemma-python/lemma_sdk/openapi_spec.json",
        json_path=("info", "version"),
    ),
    Source("lemma-typescript package", REPO_ROOT / "lemma-typescript/package.json"),
    Source(
        "lemma-typescript SDK_VERSION",
        REPO_ROOT / "lemma-typescript/src/version.ts",
        re.compile(r'SDK_VERSION = "([^"]+)"'),
    ),
    Source(
        "lemma-typescript generated client VERSION",
        REPO_ROOT / "lemma-typescript/src/openapi_client/core/OpenAPI.ts",
        re.compile(r"VERSION: '([^']+)'"),
    ),
    Source(
        "lemma-cli lemma-sdk dependency floor",
        REPO_ROOT / "lemma-cli/pyproject.toml",
        re.compile(r'"lemma-sdk>=([^"]+)"'),
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expect",
        help="Require this version too, e.g. the release tag. A leading 'v' is stripped.",
    )
    args = parser.parse_args()

    expected = args.expect.removeprefix("v").strip() if args.expect else None
    if expected is not None and not SEMVER.fullmatch(expected):
        print(f"error: --expect {args.expect!r} is not a semver version", file=sys.stderr)
        return 2

    readings: list[tuple[Source, str | None]] = [(s, s.read()) for s in SOURCES]

    problems: list[str] = []
    for source, value in readings:
        rel = source.path.relative_to(REPO_ROOT)
        if value is None:
            problems.append(f"  {source.label}: no version found in {rel}")

    found = {value for _, value in readings if value is not None}
    # Compare against the tag when given, otherwise against whatever the repo
    # already agrees on — so this is useful locally, with no release in sight.
    baseline = expected if expected is not None else (next(iter(found)) if len(found) == 1 else None)

    if baseline is not None:
        for source, value in readings:
            if value is not None and value != baseline:
                rel = source.path.relative_to(REPO_ROOT)
                problems.append(f"  {source.label}: {value} (expected {baseline}) — {rel}")
    elif len(found) > 1:
        for source, value in readings:
            if value is not None:
                rel = source.path.relative_to(REPO_ROOT)
                problems.append(f"  {source.label}: {value} — {rel}")

    if problems:
        header = (
            f"Components disagree with the release version {baseline}:"
            if expected is not None
            else "Components do not all declare the same version:"
        )
        print(header, file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        print(
            "\nSet them all to the version being released, regenerate the SDKs\n"
            "(lemma-python/scripts/generate_openapi_client.sh) and commit.",
            file=sys.stderr,
        )
        return 1

    print(f"All {len(readings)} components declare {baseline}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
