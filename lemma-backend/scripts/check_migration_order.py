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

A third check came from Desktop: nightlies install on real machines and run
``alembic upgrade head`` there, so a migration is *shipped* as soon as a
nightly carries it -- not only at a stable tag. Editing or deleting one after
that leaves every machine that already ran it on a schema the code no longer
describes, and nothing re-runs it. The newest shipped tag (``desktop-nightly-*``
or ``v*``) that is an ancestor of HEAD is the reference; every migration in it
must still be here, byte-for-byte up to line endings and trailing whitespace.
When no such tag is available locally (a shallow CI checkout, a fork) that part
is skipped and says so.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

VERSIONS = Path(__file__).resolve().parent.parent / "migrations" / "versions"
REPO_VERSIONS = "lemma-backend/migrations/versions"
SHIPPED_TAG_PATTERNS = ("refs/tags/desktop-nightly-*", "refs/tags/v*")

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
    texts: dict[str, str] = {}
    numbers: dict[int, list[str]] = {}
    problems: list[str] = []

    for path in files:
        text = path.read_text()
        revision = _REVISION.search(text)
        if revision is None:
            problems.append(f'{path.name}: no `revision = "..."` assignment')
            continue
        down_match = _DOWN_REVISION.search(text)
        revision_id = revision.group(1)
        texts.setdefault(revision_id, text)
        if revision_id in by_revision:
            # Recorded rather than overwritten: replacing the first file would
            # hide it from every check below, so the gate could walk a shortened
            # chain and report success on a directory alembic refuses to load.
            previous, _ = by_revision[revision_id]
            problems.append(
                f"{path.name}: duplicate revision id {revision_id!r}, "
                f"already declared by {previous.name}"
            )
        else:
            by_revision[revision_id] = (
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

    reference = newest_shipped_reference()
    if reference is None:
        print("  (no shipped tag available locally; shipped-migration check skipped)")
    else:
        problems.extend(shipped_edits(texts, shipped_migrations(reference), reference))

    if problems:
        print("Migration order check failed:")
        for problem in problems:
            print(f"- {problem}")
        return 1

    print(
        f"✓ migrations: {len(by_revision)} in one chain, one head, "
        f"filename numbers match apply order "
        f"({len(LEGACY_ID_POSITION_MISMATCH)} legacy id mismatches)"
        + (f", none edited since {reference}" if reference else "")
    )
    return 0


def _normalise(text: str) -> str:
    return "\n".join(
        line.rstrip() for line in text.replace("\r\n", "\n").strip().split("\n")
    )


def shipped_edits(
    current: dict[str, str], shipped: dict[str, str], reference: str
) -> list[str]:
    """Every shipped migration that is gone or changed, by revision id."""
    problems = []
    for revision, text in sorted(shipped.items()):
        if revision not in current:
            problems.append(
                f"revision {revision!r} shipped in {reference} and is no longer here; "
                "machines that ran it cannot be migrated forward -- restore it"
            )
        elif _normalise(current[revision]) != _normalise(text):
            problems.append(
                f"revision {revision!r} was edited after it shipped in {reference}; "
                "machines that already ran it will never run the edit -- "
                "revert it and add a new migration instead"
            )
    return problems


def _git(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=VERSIONS,
        capture_output=True,
        text=True,
        check=False,
    )


def newest_shipped_reference() -> str | None:
    """The newest shipped tag this checkout descends from, if it has one."""
    listing = _git(
        "for-each-ref",
        "--sort=-creatordate",
        "--count=40",
        "--format=%(refname:short)",
        *SHIPPED_TAG_PATTERNS,
    )
    if listing.returncode != 0:
        return None
    for tag in listing.stdout.split():
        if _git("merge-base", "--is-ancestor", tag, "HEAD").returncode == 0:
            return tag
    return None


def shipped_migrations(reference: str) -> dict[str, str]:
    """Revision id -> file text, for every migration in ``reference``."""
    listing = _git("ls-tree", "--name-only", f"{reference}:{REPO_VERSIONS}")
    shipped: dict[str, str] = {}
    for name in listing.stdout.split():
        if not name.endswith(".py") or name == "__init__.py":
            continue
        shown = _git("show", f"{reference}:{REPO_VERSIONS}/{name}")
        revision = _REVISION.search(shown.stdout)
        if shown.returncode == 0 and revision is not None:
            shipped[revision.group(1)] = shown.stdout
    return shipped


def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


if __name__ == "__main__":
    sys.exit(main())
