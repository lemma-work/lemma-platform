from __future__ import annotations

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
