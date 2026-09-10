"""Record what one pytest collection actually found, and stop there.

Loaded with `-p` by `check_pytest_census.py`. It exists because `--collect-only`
prints node ids and nothing about markers, and the question the census asks is
entirely about markers: which suites are still being collected, and which of
them are being collected into a lane that reports "passed".

Writes `{marker: [nodeid, ...]}` to `$PYTEST_CENSUS_OUT`.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict


def pytest_collection_modifyitems(items):
    destination = os.environ.get("PYTEST_CENSUS_OUT")
    if not destination:
        return
    # A set per marker, not a list. `iter_markers()` walks the node chain and
    # yields one entry per *application*, so a test that gets `e2e` from its
    # module's `pytestmark` and again from the root conftest's `add_marker`
    # appeared twice -- and the census then reported 2152 e2e tests where
    # pytest selects 1092. The counts are compared against a recorded floor and
    # printed as "marker X collects N tests", so the inflation was invisible
    # while every number in the message was wrong.
    by_marker: dict[str, set[str]] = defaultdict(set)
    for item in items:
        for mark in item.iter_markers():
            by_marker[mark.name].add(item.nodeid)
    # `_collected` is not a marker anyone writes, so it cannot collide with one.
    # It carries the total, which is how a collection that imported nothing at
    # all is told apart from one where every marker legitimately went to zero.
    by_marker["_collected"] = {str(len(items))}
    with open(destination, "w", encoding="utf-8") as handle:
        json.dump({name: sorted(ids) for name, ids in by_marker.items()}, handle)
