"""Every container the test helpers remove must take its volumes with it.

An image that declares VOLUME creates an anonymous volume on each `docker run`
-- pgvector declares the postgres data directory -- and `docker rm` WITHOUT
`-v` orphans it permanently: nothing names it, no label matches it, and it
holds a whole database. Hundreds of them accumulated exactly that way, tens of
GB, until Docker started failing runs.

These assert the flag rather than the behaviour because the behaviour needs a
Docker daemon; the flag is the entire fix, and dropping it is the exact
regression that caused the leak.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_HELPERS = (
    Path(__file__).resolve().parents[3] / "core" / "test_utils.py",
    Path(__file__).resolve().parents[3] / "modules" / "test_support" / "e2e_base.py",
)
# Matches a `docker rm` argument list, capturing the flags that follow.
_DOCKER_RM = re.compile(r'"docker",\s*"rm"((?:,\s*"[^"]*")*)')


@pytest.mark.parametrize("source", _HELPERS, ids=lambda path: path.name)
def test_every_docker_rm_releases_anonymous_volumes(source: Path) -> None:
    text = source.read_text(encoding="utf-8")
    invocations = _DOCKER_RM.findall(text)
    assert invocations, f"expected {source.name} to remove containers"

    missing = [args for args in invocations if '"-v"' not in args]
    assert not missing, (
        f"{source.name} removes a container without `-v`, which orphans its "
        f"anonymous volumes forever: docker rm{missing[0]}"
    )


def test_sandbox_volumes_are_swept_by_label_not_by_pruning() -> None:
    """A blanket `docker volume prune` is machine-wide.

    It would take an unrelated project's disks with it on a shared developer
    machine, so the sweep is scoped to the workspace label.
    """
    text = _HELPERS[1].read_text(encoding="utf-8")

    assert "label=managed-by=lemma-workspace" in text
    assert '"prune"' not in text
