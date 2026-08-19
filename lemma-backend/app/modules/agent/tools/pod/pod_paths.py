"""Present pod file paths to the agent in the ``/me/...`` alias form.

The datastore service layer returns files with their raw ``/{user-uuid}/...``
storage path. The HTTP controllers re-map that to ``/me/...`` before it leaves
the API, but the agent pod tools consume the service directly and so used to
hand the model a raw user UUID — different from what ``pod_search_files`` (which
normalizes internally) reported for the very same file.

Normalizing here, at the tool boundary rather than in the shared service, keeps
the HTTP path untouched: the controllers already alias, and doing it twice would
turn ``/me/x`` into ``/me/me/x``. This mirrors ``PathResolver._to_api_path``.
"""

from __future__ import annotations

from typing import Any

from app.modules.datastore.services.files.paths import normalize_datastore_path


def to_me_path(path: str | None, user_id: Any) -> str:
    """Rewrite a raw ``/{user_id}/...`` path to its ``/me/...`` alias.

    Paths that are not under the requester's personal root (shared, knowledge,
    already-aliased) are returned normalized but otherwise unchanged.
    """
    normalized = normalize_datastore_path(path)
    personal_root = f"/{user_id}"
    if normalized == personal_root:
        return "/me"
    if normalized.startswith(f"{personal_root}/"):
        return f"/me{normalized.removeprefix(personal_root)}"
    return normalized


_PATH_KEYS = {"path", "parent_path"}


def normalize_json_paths(value: Any, user_id: Any) -> Any:
    """Rewrite every ``path``/``parent_path`` string in a JSON-able structure.

    Used for the nested directory-tree payload, whose every node carries a raw
    path there is no single field to fix.
    """
    if isinstance(value, dict):
        return {
            key: (
                to_me_path(item, user_id)
                if key in _PATH_KEYS and isinstance(item, str)
                else normalize_json_paths(item, user_id)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [normalize_json_paths(item, user_id) for item in value]
    return value
