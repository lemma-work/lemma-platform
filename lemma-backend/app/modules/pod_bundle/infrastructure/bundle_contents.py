"""How many resources a bundle holds, counted the same way at both ends.

``bundle.exported`` and ``import.completed`` each report a resource count, and
the two are only worth comparing if they count the same thing. The export's was
read off a job-state attribute that does not exist, so it was always zero; the
import's was the number of plan steps, which counts a table's rows, an agent's
grants and every file as steps of their own.

One resource is one ``<kind>/<name>/`` directory of a kind the plan builder
turns into a resource: a connector, table, function, agent, workflow,
schedule, surface or app. Rows, grants and ``files/`` belong to those rather
than being resources, so a bundle that carries three hundred PDFs is not a
bundle of three hundred things.

The export counts the archive it packed; the import counts the tree it
extracted. Both read the layout ``plan_builder`` reads.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from lemma_pod_bundle.layout import RESOURCE_DIRS

#: Every resource directory kind except ``files``, which holds content.
RESOURCE_KINDS = frozenset(RESOURCE_DIRS) - {"files"}


def resource_count(bundle_root: Path) -> int:
    """Resources in an extracted bundle: its ``<kind>/<name>/`` directories."""
    return sum(
        1
        for kind in RESOURCE_KINDS
        if (bundle_root / kind).is_dir()
        for entry in (bundle_root / kind).iterdir()
        if entry.is_dir()
    )


def archived_resource_count(archive: bytes) -> int:
    """Resources in a packed bundle, by the same rule as ``resource_count``.

    A member path with a third segment sits inside a ``<kind>/<name>/``
    directory, which is what makes ``name`` a resource rather than a stray file
    at the top of its kind.
    """
    with ZipFile(BytesIO(archive)) as bundle:
        names = bundle.namelist()
    resources = {
        (parts[0], parts[1])
        for parts in (name.split("/") for name in names)
        if len(parts) >= 3 and parts[0] in RESOURCE_KINDS and parts[1]
    }
    return len(resources)


__all__ = ["RESOURCE_KINDS", "archived_resource_count", "resource_count"]
