"""Every module's tests live in a directory that says which lane they are in.

`schedule` had 31 unit-shaped files sitting directly under `tests/`, and the
consequences were not cosmetic. `UNIT_MARKERS` selects on markers rather than
paths, so they ran -- but no directory-based tool could tell them apart from
e2e ones. The module needed the only hand-written coverage floor in the repo
because it was the one whose layout tooling could not reason about, and
`plan_e2e_shards.py` could not see the files at all: an e2e test dropped in
beside them would have been collected by the unit lane and never sharded.

So the shape is the contract. A file that is not under `unit/` or `e2e/` (or
one of the named lanes below) is one no tool can route.
"""

from __future__ import annotations

from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[3]
_MODULES = _BACKEND / "app" / "modules"

# Lanes that exist for a reason and are selected elsewhere: `integration` needs
# Docker/E2B and `desktop_e2e` needs a built app, both by marker; `perf` is the
# function benchmark, run by path from the Makefile and never in a test lane.
_LANES = {"unit", "e2e", "integration", "desktop_e2e", "perf"}


def test_no_module_leaves_test_files_outside_a_lane() -> None:
    stray: dict[str, list[str]] = {}
    for tests_dir in sorted(_MODULES.glob("*/tests")):
        loose = sorted(path.name for path in tests_dir.glob("test_*.py"))
        if loose:
            stray[tests_dir.parent.name] = loose

    assert not stray, (
        f"test files sit directly under tests/ rather than in a lane "
        f"({sorted(_LANES)}): { {module: files[:3] for module, files in stray.items()} }. "
        f"Nothing routes them — the unit lane collects them by marker while "
        f"the e2e sharder cannot see them, so an e2e test landing there would "
        f"run unsharded in the wrong lane."
    )


def test_every_test_directory_a_module_has_is_a_known_lane() -> None:
    unknown: dict[str, list[str]] = {}
    for tests_dir in sorted(_MODULES.glob("*/tests")):
        extra = sorted(
            path.name
            for path in tests_dir.iterdir()
            if path.is_dir()
            and not path.name.startswith("__")
            and path.name not in _LANES
            and any(path.rglob("test_*.py"))
        )
        if extra:
            unknown[tests_dir.parent.name] = extra

    assert not unknown, (
        f"test directories that are not a lane any tool knows about: {unknown}. "
        f"Add the lane to _LANES here and to whatever selects it, or move the "
        f"files into an existing one."
    )
