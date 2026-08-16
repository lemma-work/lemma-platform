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


# One member is read into memory at a time, so a crafted archive claiming a
# petabyte for one entry cannot exhaust the importer. Well above any real built
# asset; a bundle with a bigger one is simply rebuilt.
_MAX_SCANNED_MEMBER_BYTES = 64 * 1024 * 1024


def _pod_id_forms(pod_id: str) -> tuple[bytes, ...]:
    """Every textual form the id could survive a build in.

    A minifier or bundler can re-emit a UUID without its hyphens, and a build
    that stores it in a URL path leaves the bare hex. Scanning only the
    canonical form would call such a build portable and ship it pointed at the
    pod it was exported from.
    """
    canonical = str(pod_id).strip().lower()
    if not canonical:
        return ()
    forms = [canonical]
    # Only worth adding when it is long enough to be an id rather than a
    # substring that could occur by chance. The canonical form is never dropped
    # for being short -- doing so would leave nothing to search for, and every
    # build would come back "rebuild it".
    hyphenless = canonical.replace("-", "")
    if hyphenless != canonical and len(hyphenless) >= 16:
        forms.append(hyphenless)
    return tuple(form.encode() for form in forms)


def dist_is_portable(dist_archive: bytes | Path, *, pod_id: str) -> bool:
    """True when ``dist_archive`` bakes in nothing specific to ``pod_id``.

    This can only ever prove NON-portability. A build that hardcodes something
    else pod-specific -- a table id, an agent name, an API host it reaches
    without the SDK -- passes, because nothing in the bytes identifies it as
    belonging to the source pod. That is why the failure has to fall on the safe
    side everywhere else: an unreadable archive, an oversized member, or an
    absent pod id all report NOT portable, so the importer rebuilds, which is
    what it did before this check existed.
    """
    needles = _pod_id_forms(pod_id)
    if not needles:
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
                if info.file_size > _MAX_SCANNED_MEMBER_BYTES:
                    return False
                content = archive.read(info).lower()
                if any(needle in content for needle in needles):
                    return False
    except (zipfile.BadZipFile, OSError, KeyError):
        return False
    return True
