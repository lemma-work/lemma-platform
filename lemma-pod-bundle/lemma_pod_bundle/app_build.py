"""An app that comes already built.

A bundled app with a ``source/`` project (a ``package.json``) is rebuilt by
whoever imports it: install, build, upload. That is the slow part of an import,
minutes for a bundle with a few apps. A bundle may carry the build beside the
source instead::

    apps/<name>/source/              the project, as ever
    apps/<name>/dist.zip             what ``source/`` builds to
    apps/<name>/dist.source-digest   ``sha256:<hex>``: the source it was built from

and an importer deploys that ``dist.zip`` as it is, without building, while the
digest still names exactly the ``source/`` it stores (:func:`prebuilt_dist`).
Edit the source and forget the build, and the digest no longer matches: the
import builds from source, as it would with no ``dist.zip`` at all.

The digest is the bundle author's word that the build reads everything that
differs between Lemmas -- the Lemma, the API's address -- from the page at serve
time (``window.__LEMMA_CONFIG__``), not from values fixed at build time. Only
whoever built it can say so, which is why an export, which cannot tell, writes
no digest and its apps keep being rebuilt.

Not a ``.json``: an app's manifest is ``<name>.json``, and an app named
``dist`` would have read the stamp as its manifest.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

APP_DIST_ARCHIVE = "dist.zip"
APP_DIST_SOURCE_DIGEST = "dist.source-digest"

#: Never part of an app's source: what a build, an install or a checkout leaves
#: in the tree. The importer stores the source without them, so the digest is
#: of the same files.
APP_SOURCE_EXCLUDE_DIRS = frozenset(
    {
        "node_modules",
        ".git",
        "dist",
        "build",
        ".next",
        ".turbo",
        ".cache",
        "coverage",
        "__pycache__",
    }
)

_DIGEST_PREFIX = "sha256:"


def app_source_files(source_dir: Path) -> list[tuple[str, Path]]:
    """Every file of an app's source as ``(posix path below source_dir, path)``,
    in path order, without :data:`APP_SOURCE_EXCLUDE_DIRS`."""
    files: list[tuple[str, Path]] = []
    for path in source_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(source_dir)
        if any(part in APP_SOURCE_EXCLUDE_DIRS for part in rel.parts):
            continue
        files.append((rel.as_posix(), path))
    files.sort(key=lambda item: item[0])
    return files


def app_source_digest(source_dir: Path) -> str:
    """``sha256:<hex>`` of an app's source: each file's path and the sha256 of
    its bytes, in path order. Independent of timestamps, permissions and how
    the tree was zipped, so the same files give the same digest anywhere.

    Exactly: sha256 over, for each file, its posix path encoded UTF-8, a NUL
    byte, then the 32-byte sha256 of its contents. A path holds no NUL and the
    file digest has a fixed length, so no two trees run together.
    """
    digest = hashlib.sha256()
    for rel, path in app_source_files(source_dir):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return _DIGEST_PREFIX + digest.hexdigest()


def read_dist_source_digest(resource_dir: Path) -> str | None:
    """The digest an app's ``dist.source-digest`` names, or None when it has
    none or it does not read as one."""
    stamp = resource_dir / APP_DIST_SOURCE_DIGEST
    if not stamp.is_file():
        return None
    try:
        value = stamp.read_text(encoding="utf-8").strip()
    except OSError, UnicodeDecodeError:
        return None
    hex_part = value.removeprefix(_DIGEST_PREFIX)
    if not value.startswith(_DIGEST_PREFIX) or len(hex_part) != 64:
        return None
    try:
        int(hex_part, 16)
    except ValueError:
        return None
    return value


def write_dist_source_digest(resource_dir: Path, digest: str) -> None:
    (resource_dir / APP_DIST_SOURCE_DIGEST).write_text(digest + "\n", encoding="utf-8")


def prebuilt_dist(resource_dir: Path) -> Path | None:
    """The app's ``dist.zip`` when it was built from exactly its ``source/``,
    else None (no source, no build, no digest, or a digest of other source)."""
    source_dir = resource_dir / "source"
    dist = resource_dir / APP_DIST_ARCHIVE
    if not source_dir.is_dir() or not dist.is_file():
        return None
    stamped = read_dist_source_digest(resource_dir)
    if stamped is None or stamped != app_source_digest(source_dir):
        return None
    return dist
