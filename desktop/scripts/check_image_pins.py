#!/usr/bin/env python3
"""The guest image is built from the same bytes every time, or it is not the same guest.

`ubuntu:24.04` is a moving tag: Canonical rebuilds it, and the same Lemma commit
then produces a different Linux on different days. The one thing this image is
*for* is being identical on every machine that installs Lemma, so a base that
changes under a fixed release is a difference nobody can see and nobody can
bisect -- and the guest is where the database, the container runtime and every
sandbox live.

So every `FROM` that names a registry image must carry a `@sha256:` digest.
`scratch` is the exception and the only one: it is not an image, it is the
absence of one.

The tag stays beside the digest on purpose. It is what a person reads, and what
Dependabot matches on when it opens the pull request that moves both.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parent.parent

DOCKERFILES = [DESKTOP / "local-runtime/guest-image/Dockerfile"]

# `FROM <image>[:tag][@digest] [AS stage]`, with the ARG-less forms this repo
# uses. A `FROM $BUILDER` referring to an earlier stage is not a registry
# reference and is left alone.
# Case-insensitive, because Dockerfile instructions are: `from ubuntu:24.04`
# is the same instruction as `FROM`, and a checker that only knows one spelling
# is a checker somebody can step around without meaning to.
FROM_LINE = re.compile(
    r"^FROM\s+(?P<image>\S+)(?:\s+AS\s+(?P<stage>\S+))?\s*$", re.IGNORECASE
)

DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def _readable(path: Path) -> str:
    """Repo-relative where that means something, absolute where it does not."""
    try:
        return str(path.relative_to(DESKTOP.parent))
    except ValueError:
        return str(path)


def failures() -> list[str]:
    problems: list[str] = []
    for dockerfile in DOCKERFILES:
        stages: set[str] = set()
        for number, line in enumerate(dockerfile.read_text().splitlines(), start=1):
            match = FROM_LINE.match(line.strip())
            if not match:
                continue
            image = match.group("image")
            # Decide about this line's image *before* recording its stage. The
            # other order let `FROM ubuntu AS ubuntu` add "ubuntu" to the stage
            # set and then match itself as an earlier stage, so the one line
            # that names a moving tag was the one line exempted from the check.
            known = image.lower() == "scratch" or image in stages or image.startswith("$")
            if match.group("stage"):
                stages.add(match.group("stage"))
            if known:
                continue
            if not DIGEST.search(image):
                problems.append(
                    f"{_readable(dockerfile)}:{number}: "
                    f"{image} is a moving tag. Pin it as "
                    f"`<image>:<tag>@sha256:<digest>` so the same commit builds "
                    f"the same guest."
                )
    return problems


def main() -> int:
    problems = failures()
    for problem in problems:
        print(f"✗ {problem}", file=sys.stderr)
    if problems:
        return 1
    print("✓ Guest image: every base is pinned by digest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
