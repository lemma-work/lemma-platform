#!/usr/bin/env python3
"""Scripts CI runs with a bare `python` must parse on an old one.

The backend pins Python 3.14 and uses its syntax deliberately -- PEP 758's
unparenthesised `except A, B:` among it. `scripts/` is different: CI invokes
these with whatever `python` the runner provides, which on the Windows and
macOS images is not 3.14. Syntax the runner cannot parse is not a failing
build step, it is a `SyntaxError` before the first line runs.

That is not hypothetical. One unparenthesised `except` reached
`build_local_host_pack.py` and every Windows host pack stopped building --
in the same change whose title was about unbreaking the host pack. The
repository's own CLAUDE.md warns about this exact trap from the other
direction, where an old interpreter reports correct 3.14 code as broken.

So this parses each script under an older grammar and fails naming the file
and the line. It does not run anything, and it makes no claim about
behaviour: only that CI can get as far as executing it.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The oldest interpreter a CI runner has plausibly offered. Raising this is a
#: decision about which runners are still supported, not a formality.
OLDEST_SUPPORTED = (3, 9)


def main() -> int:
    failures: list[str] = []
    checked = 0
    for script in sorted(ROOT.joinpath("scripts").glob("*.py")):
        source = script.read_text(encoding="utf-8")
        checked += 1
        try:
            ast.parse(source, filename=str(script), feature_version=OLDEST_SUPPORTED)
        except SyntaxError as error:
            failures.append(f"- {script.relative_to(ROOT)}:{error.lineno}: {error.msg}")

    version = ".".join(str(part) for part in OLDEST_SUPPORTED)
    if failures:
        print(
            f"Scripts must parse on Python {version}; CI runs them with a bare `python`:"
        )
        print("\n".join(failures))
        print(
            "\nThe backend may use 3.14 syntax because it pins 3.14. These may not: "
            "a runner that cannot parse the file fails before the first statement, "
            "and the error names a line rather than the thing that broke."
        )
        return 1

    print(f"{checked} scripts parse on Python {version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
