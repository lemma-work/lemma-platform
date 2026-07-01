"""Bundle staging — where an uploaded archive lives between plan and apply.

A simple local-filesystem implementation keyed by import id: extract the archive
once on create, read it again on apply/resume. This is the storage seam — a
production deployment swaps it for blob storage so any instance can resume — but
the interface (stage / path_for) stays the same.
"""

from __future__ import annotations

import io
import tarfile
import tempfile
import zipfile
from pathlib import Path
from uuid import UUID

_DEFAULT_ROOT = Path(tempfile.gettempdir()) / "lemma-pod-imports"


class BundleStaging:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _DEFAULT_ROOT

    def stage(self, import_id: UUID, archive: bytes, filename: str | None) -> Path:
        """Extract an uploaded archive into this import's staging dir and return
        the bundle root (the directory holding pod.json)."""
        dest = self.root / str(import_id)
        dest.mkdir(parents=True, exist_ok=True)
        _extract(archive, filename or "", dest)
        return self._bundle_root(dest)

    def path_for(self, import_id: UUID) -> Path | None:
        dest = self.root / str(import_id)
        return self._bundle_root(dest) if dest.is_dir() else None

    def _bundle_root(self, extracted: Path) -> Path:
        """The directory containing pod.json — the extraction root, or the
        shallowest descendant that has one. An export archive wraps everything
        in one folder, and a GitHub codeload zip adds its own wrapper on top of
        that, so two levels of nesting is normal for a repo-published bundle;
        this isn't limited to one level down."""
        if (extracted / "pod.json").is_file():
            return extracted
        matches = sorted(
            extracted.rglob("pod.json"), key=lambda p: len(p.relative_to(extracted).parts)
        )
        return matches[0].parent if matches else extracted


def _is_within(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _extract(archive: bytes, filename: str, dest: Path) -> None:
    """Extract a .zip or .tar(.gz) archive, guarding against path traversal."""
    lowered = filename.lower()
    if lowered.endswith(".zip") or (not lowered and _looks_zip(archive)):
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            for member in zf.namelist():
                out = dest / member
                if not _is_within(dest, out):
                    raise ValueError(f"Unsafe path in archive: {member}")
            zf.extractall(dest)
        return
    mode = "r:gz" if lowered.endswith((".tar.gz", ".tgz")) else "r:*"
    with tarfile.open(fileobj=io.BytesIO(archive), mode=mode) as tf:
        for member in tf.getmembers():
            if not _is_within(dest, dest / member.name):
                raise ValueError(f"Unsafe path in archive: {member.name}")
        tf.extractall(dest)


def _looks_zip(archive: bytes) -> bool:
    return archive[:2] == b"PK"


def has_pod_manifest(archive: bytes, filename: str | None = None) -> bool:
    """Whether the archive contains a pod.json at all — distinct from
    ``peek_pod_manifest`` returning ``{}``, which is ambiguous between "no
    pod.json" and "a pod.json whose content happens to be an empty object"."""
    return _read_archive_member(archive, filename or "", "pod.json") is not None


def peek_pod_manifest(archive: bytes, filename: str | None = None) -> dict:
    """Read just ``pod.json`` from an archive in memory — enough to name a new
    pod before the bundle is staged. Returns the parsed manifest (name,
    description, icon), or ``{}`` if there's no readable pod.json."""
    from lemma_pod_bundle import loads_jsonc

    raw = _read_archive_member(archive, filename or "", "pod.json")
    if raw is None:
        return {}
    try:
        data = loads_jsonc(raw.decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_archive_member(archive: bytes, filename: str, basename: str) -> bytes | None:
    """Return the bytes of the shallowest archive entry named ``basename``."""
    lowered = filename.lower()
    if lowered.endswith(".zip") or (not lowered and _looks_zip(archive)):
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            names = [n for n in zf.namelist() if n.rsplit("/", 1)[-1] == basename]
            if not names:
                return None
            return zf.read(min(names, key=lambda n: n.count("/")))
    mode = "r:gz" if lowered.endswith((".tar.gz", ".tgz")) else "r:*"
    with tarfile.open(fileobj=io.BytesIO(archive), mode=mode) as tf:
        cand = [m for m in tf.getmembers() if m.isfile() and m.name.rsplit("/", 1)[-1] == basename]
        if not cand:
            return None
        member = min(cand, key=lambda m: m.name.count("/"))
        extracted = tf.extractfile(member)
        return extracted.read() if extracted else None
