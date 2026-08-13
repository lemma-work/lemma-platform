"""Whether an exported app build can be reused in another pod.

An app bundle used to carry EITHER source or a build, never both, so importing
one always meant rebuilding a Vite app in a sandbox -- minutes, and a sandbox
dependency -- on the premise that a build bakes its pod id in.

That premise is only sometimes true. The scaffolded client is
``new LemmaClient()`` with no overrides, and the browser SDK's ``resolveConfig``
prefers the ``window.__LEMMA_CONFIG__`` the host injects at serve time over
``import.meta.env``. A build from the template therefore runs unchanged on any
pod. Only an app whose own code reads ``import.meta.env.VITE_LEMMA_POD_ID`` (or
otherwise hardcodes an id) is genuinely stuck to the pod that built it.

That distinction is decidable by looking: if the source pod's id appears nowhere
in the built bytes, nothing pod-specific was baked in. The check is deliberately
a plain substring scan rather than anything clever -- a false "not portable"
costs a rebuild we would have done anyway, while a false "portable" ships a
broken app, so the failure has to fall on the safe side.
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

# Text-ish members are the only place a baked id can appear in a form that
# matters; scanning images and fonts wastes time on megabytes that cannot
# contain a UUID in a meaningful way.
_SCANNED_SUFFIXES = frozenset(
    {".js", ".mjs", ".cjs", ".html", ".htm", ".css", ".json", ".txt", ".map", ".svg"}
)


def dist_is_portable(dist_archive: bytes | Path, *, pod_id: str) -> bool:
    """True when ``dist_archive`` bakes in nothing specific to ``pod_id``.

    An unreadable archive is reported as NOT portable: the importer then
    rebuilds, which is what it would have done before this check existed.
    """
    needle = str(pod_id).strip().lower()
    if not needle:
        return False
    try:
        source = (
            dist_archive if isinstance(dist_archive, Path) else BytesIO(dist_archive)
        )
        with zipfile.ZipFile(source) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if Path(info.filename).suffix.lower() not in _SCANNED_SUFFIXES:
                    continue
                content = archive.read(info)
                if needle.encode() in content.lower():
                    return False
    except (zipfile.BadZipFile, OSError, KeyError):
        return False
    return True
