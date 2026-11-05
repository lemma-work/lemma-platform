from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


def _to_storage_segment(path: str) -> str:
    """Strip surrounding slashes to turn a datastore path into an object-key
    segment. This is distinct from ``normalize_datastore_path`` (which keeps a
    leading ``/`` and validates segments) — here we only build storage keys.
    """
    return path.strip("/")


def _split_parent_and_name(path: str) -> tuple[str, str]:
    """Split a datastore path into (parent storage segment, leaf name)."""
    segment = _to_storage_segment(path)
    if "/" in segment:
        parent, name = segment.rsplit("/", 1)
        return parent, name
    return "", segment


def build_datastore_file_storage_key(
    pod_id: UUID,
    path: str,
) -> str:
    normalized_path = _to_storage_segment(path)
    if not normalized_path:
        return f"pods/{pod_id}/files"
    return f"pods/{pod_id}/files/{normalized_path}"


def build_datastore_folder_storage_prefix(
    pod_id: UUID,
    path: str,
) -> str:
    base = build_datastore_file_storage_key(pod_id, path)
    return f"{base.rstrip('/')}/"


# ---------------------------------------------------------------------------
# Derived "child" artifacts (converted markdown, extracted figures, rendered
# page images) are colocated next to the source file under the single
# ``files/`` root, inside a hidden, dot-prefixed container keyed by the file
# name. This keeps everything for a pod under one bucket prefix (so a single
# prefix copy is a complete backup) while staying out of directory listings
# (the dot-prefix is the reserved "hidden/derived" convention). For a file at
# ``/folder/report.pdf`` the container is
# ``pods/{pod}/files/folder/.report.pdf/`` holding:
#   manifest.json, document.md, image_0.png, …, pages/page_0001.jpg
# ---------------------------------------------------------------------------

# Subdirectory (inside a child container) for on-demand rendered page images.
_CHILD_PAGES_DIR = "pages"
# Name of the converted-markdown artifact inside a child container.
CHILD_MARKDOWN_ARTIFACT = "document.md"
# Name of the manifest artifact inside a child container.
CHILD_MANIFEST_ARTIFACT = "manifest.json"
# Name of the user-provided ("bring your own") markdown source inside a child
# container. Unlike document.md (derived), this is user-authored source data: the
# processor chunks/indexes it on behalf of the original document when present.
# delete_child_artifacts wipes the whole container on every reprocess, so the
# processor re-persists this on each projection write (it holds the bytes).
CHILD_USER_MARKDOWN_ARTIFACT = "source.md"


def build_datastore_child_container_prefix(
    pod_id: UUID,
    path: str,
) -> str:
    """Hidden, colocated container holding a file's derived child artifacts."""
    parent, name = _split_parent_and_name(path)
    base = f"pods/{pod_id}/files"
    if parent:
        return f"{base}/{parent}/.{name}/"
    return f"{base}/.{name}/"


def build_datastore_child_artifact_key(
    pod_id: UUID,
    path: str,
    artifact_name: str,
) -> str:
    prefix = build_datastore_child_container_prefix(pod_id, path)
    return f"{prefix}{artifact_name.lstrip('/')}"


def build_datastore_child_manifest_key(
    pod_id: UUID,
    path: str,
) -> str:
    return build_datastore_child_artifact_key(pod_id, path, CHILD_MANIFEST_ARTIFACT)


def build_datastore_child_markdown_key(
    pod_id: UUID,
    path: str,
) -> str:
    return build_datastore_child_artifact_key(pod_id, path, CHILD_MARKDOWN_ARTIFACT)


def build_datastore_child_user_markdown_key(
    pod_id: UUID,
    path: str,
) -> str:
    """Storage key for a file's user-provided markdown source (``source.md``)."""
    return build_datastore_child_artifact_key(
        pod_id, path, CHILD_USER_MARKDOWN_ARTIFACT
    )


def child_page_artifact_name(page_number: int) -> str:
    """Relative artifact name (within a child container) for a rendered page."""
    return f"{_CHILD_PAGES_DIR}/page_{page_number:04d}.jpg"


def is_child_page_artifact(artifact_name: str) -> bool:
    return artifact_name.lstrip("/").startswith(f"{_CHILD_PAGES_DIR}/")


def build_datastore_child_page_key(
    pod_id: UUID,
    path: str,
    page_number: int,
) -> str:
    """Storage key for a single cached, rendered page image (1-based page)."""
    return build_datastore_child_artifact_key(
        pod_id,
        path,
        child_page_artifact_name(page_number),
    )


#: A rendered page artifact is named `page_0001.jpg`; this reads the number back.
#: It lives here with `is_child_page_artifact` rather than in the file service,
#: which is a facade and had no other reason to know the naming convention.
_CHILD_PAGE_RE = re.compile(r"page_(\d+)\.jpg$")


def child_page_number(artifact_rel: str) -> int | None:
    """The 1-based page number in a rendered page artifact's name, if it is one."""
    match = _CHILD_PAGE_RE.search(artifact_rel)
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Object layout. A file's bytes keyed by what they are rather than where they
# sit: ``pods/{pod}/objects/{file_id}/{sha256}/content`` for the original and
# ``…/{sha256}/derived/`` for everything made from it. A rename changes neither
# key, so it is a metadata update; a reprocess writes a new revision's prefix
# and readers switch to it when the row says it is indexed, so it is atomic.
#
# ``content`` rather than the bare digest as the original's name: a key that is
# both an object and a prefix is fine in an object store and impossible on the
# local filesystem backend.
# ---------------------------------------------------------------------------

PATH_LAYOUT = "path"
OBJECT_LAYOUT = "object"
_NO_DIGEST = "unhashed"


class StoredFile(Protocol):
    """What locating a file's bytes needs to know about it."""

    @property
    def id(self) -> UUID | None: ...
    @property
    def pod_id(self) -> UUID: ...
    @property
    def path(self) -> str: ...
    @property
    def content_sha256(self) -> str | None: ...
    @property
    def indexed_sha256(self) -> str | None: ...
    @property
    def storage_layout(self) -> str: ...


@dataclass(frozen=True, slots=True)
class FileKeys:
    """Where one revision of one file lives: its original and its derived container."""

    original: str
    child_prefix: str

    def child(self, artifact_name: str) -> str:
        return f"{self.child_prefix}{artifact_name.lstrip('/')}"

    @property
    def markdown(self) -> str:
        return self.child(CHILD_MARKDOWN_ARTIFACT)

    @property
    def manifest(self) -> str:
        return self.child(CHILD_MANIFEST_ARTIFACT)

    @property
    def user_markdown(self) -> str:
        return self.child(CHILD_USER_MARKDOWN_ARTIFACT)

    def page(self, page_number: int) -> str:
        return self.child(child_page_artifact_name(page_number))


def object_file_prefix(pod_id: UUID, file_id: UUID) -> str:
    """Every revision of one file in the object layout."""
    return f"pods/{pod_id}/objects/{file_id}/"


def object_keys(pod_id: UUID, file_id: UUID, sha256: str | None) -> FileKeys:
    base = f"{object_file_prefix(pod_id, file_id)}{sha256 or _NO_DIGEST}"
    return FileKeys(original=f"{base}/content", child_prefix=f"{base}/derived/")


def path_keys(pod_id: UUID, path: str) -> FileKeys:
    return FileKeys(
        original=build_datastore_file_storage_key(pod_id, path),
        child_prefix=build_datastore_child_container_prefix(pod_id, path),
    )


def new_file_storage_layout() -> str:
    """The layout a file written now gets: the switch, not the file, decides."""
    from app.modules.datastore.config import datastore_settings

    return (
        OBJECT_LAYOUT
        if datastore_settings.datastore_object_storage_layout
        else PATH_LAYOUT
    )


def is_object_layout(file: StoredFile) -> bool:
    return getattr(file, "storage_layout", PATH_LAYOUT) == OBJECT_LAYOUT


def content_keys(file: StoredFile, *, sha256: str | None = None) -> FileKeys:
    """The revision being written or processed: the row's bytes, or ``sha256``'s."""
    if is_object_layout(file) and file.id is not None:
        return object_keys(file.pod_id, file.id, sha256 or file.content_sha256)
    return path_keys(file.pod_id, file.path)


def readable_keys(file: StoredFile) -> FileKeys:
    """The revision readers see: derived artifacts of what was last indexed.

    In the object layout the original is always the current bytes, but the
    derived container is the indexed revision's, so a document being
    reprocessed keeps serving its previous conversion until the new one lands.
    """
    if is_object_layout(file) and file.id is not None:
        original = object_keys(file.pod_id, file.id, file.content_sha256).original
        indexed = object_keys(
            file.pod_id, file.id, file.indexed_sha256 or file.content_sha256
        )
        return FileKeys(original=original, child_prefix=indexed.child_prefix)
    return path_keys(file.pod_id, file.path)


def fallback_keys(file: StoredFile) -> FileKeys | None:
    """The other layout's keys, for reads racing the background copy."""
    if file.id is None:
        return None
    if is_object_layout(file):
        return path_keys(file.pod_id, file.path)
    return object_keys(file.pod_id, file.id, file.content_sha256)
