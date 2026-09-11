#!/usr/bin/env python
"""Keep the migration directory readable: one head, and numbers that mean something.

Two things went wrong before this existed, both from long-lived branches:

- A branch authored at position 21 merged at position 30. Its author correctly
  rebased ``down_revision``, which is the part that matters, and left the
  filename saying ``0021`` — so the directory had two ``0021``s, two ``0022``s,
  and a chain that read 22 -> 30 with no file missing. Nothing was broken; it
  was simply no longer possible to tell apply order by looking.
- Nothing checked for a second head, which is the version of this that *is*
  broken and which `alembic upgrade head` refuses to run at all.

The filename number is positional and cosmetic; the ``revision`` string inside
the file is the identity, and it is what each database records in
``alembic_version``. That is why the two can be renamed independently, and why
the three files listed in ``LEGACY_ID_POSITION_MISMATCH`` still disagree: their
ids shipped, so databases are stamped with them, and renaming an id strands
every deployment sitting on it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

VERSIONS = Path(__file__).resolve().parent.parent / "migrations" / "versions"

# Files whose filename position and revision id disagree, for the historical
# reason above. Each id is already recorded in deployed `alembic_version` rows,
# so it cannot be renamed without stamping every database by hand. New entries
# do not belong here: number a new migration after the current head.
LEGACY_ID_POSITION_MISMATCH = {
    "2026-08-16_app_release_history_0030.py",
    "2026-08-16_function_revisions_0031.py",
    "2026-09-06_usage_requests_0032.py",
}

_FILE_NUMBER = re.compile(r"_(\d{4})(?:_baseline)?\.py$")
_REVISION = re.compile(r"^revision = [\"']([^\"']+)[\"']", re.M)
_DOWN_REVISION = re.compile(r"^down_revision = (?:[\"']([^\"']+)[\"']|None)", re.M)


def main() -> int:
    files = sorted(p for p in VERSIONS.glob("*.py") if p.name != "__init__.py")
    if not files:
        print(f"No migrations found under {VERSIONS}")
        return 1

    by_revision: dict[str, tuple[Path, str | None]] = {}
    numbers: dict[int, list[str]] = {}
    problems: list[str] = []

    for path in files:
        text = path.read_text()
        revision = _REVISION.search(text)
        if revision is None:
            problems.append(f'{path.name}: no `revision = "..."` assignment')
            continue
        down_match = _DOWN_REVISION.search(text)
        by_revision[revision.group(1)] = (
            path,
            down_match.group(1) if down_match else None,
        )

        number = _FILE_NUMBER.search(path.name)
        if number is None:
            problems.append(
                f"{path.name}: filename must end in _NNNN.py so its apply position is readable"
            )
            continue
        numbers.setdefault(int(number.group(1)), []).append(path.name)

    for number, names in sorted(numbers.items()):
        if len(names) > 1:
            problems.append(
                f"filename number {number:04d} used by {len(names)} files: {', '.join(sorted(names))}"
            )

    # Exactly one head: a revision nothing else points at.
    parents = {down for _path, down in by_revision.values() if down}
    heads = [rev for rev in by_revision if rev not in parents]
    if len(heads) != 1:
        problems.append(
            f"expected exactly one head, found {len(heads)}: {', '.join(sorted(heads)) or '(none)'}"
        )
    else:
        # Walk the chain back from the head so position is the chain's, not the
        # directory listing's — the whole point of the check.
        order: list[str] = []
        seen: set[str] = set()
        cursor: str | None = heads[0]
        while cursor is not None:
            if cursor in seen:
                problems.append(f"cycle in down_revision chain at {cursor}")
                break
            if cursor not in by_revision:
                problems.append(
                    f"down_revision {cursor!r} names no migration in this directory"
                )
                break
            seen.add(cursor)
            order.append(cursor)
            cursor = by_revision[cursor][1]
        order.reverse()

        if len(order) != len(by_revision):
            orphans = sorted(set(by_revision) - set(order))
            problems.append(
                f"{len(orphans)} migration(s) not reachable from the head: {', '.join(orphans)}"
            )

        for position, revision in enumerate(order, start=1):
            path, _down = by_revision[revision]
            number = _FILE_NUMBER.search(path.name)
            if number and int(number.group(1)) != position:
                problems.append(
                    f"{path.name}: applies {position}{_ordinal(position)} but the filename says "
                    f"{int(number.group(1)):04d} — rename the file (not the revision id)"
                )
            if path.name in LEGACY_ID_POSITION_MISMATCH:
                continue
            if number and not revision.startswith(f"{position:04d}_"):
                problems.append(
                    f"{path.name}: revision id {revision!r} should start with {position:04d}_ "
                    f"(it has not shipped, so the id is still free to change)"
                )

    if problems:
        print("Migration order check failed:")
        for problem in problems:
            print(f"- {problem}")
        return 1

    print(
        f"✓ migrations: {len(by_revision)} in one chain, one head, "
        f"filename numbers match apply order "
        f"({len(LEGACY_ID_POSITION_MISMATCH)} legacy id mismatches)"
    )
    return 0


def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


if __name__ == "__main__":
    sys.exit(main())
