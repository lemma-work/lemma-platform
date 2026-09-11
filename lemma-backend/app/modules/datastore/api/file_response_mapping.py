"""Turning file entities into the shapes the datastore's HTTP API returns.

Shared by the file controller and the signed-link controller, which both answer
with file paths and both have to hide the same thing: a personal file's stored
path is rooted at its owner's user id, and only that owner should see it as
``/me``.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.api.schemas.datastore_schemas import FileResponse


def to_public_file_path(
    path: str,
    *,
    current_user_id: UUID,
    owner_user_id: UUID | None,
) -> str:
    if owner_user_id == current_user_id:
        personal_root = f"/{current_user_id}"
        if path == personal_root:
            return "/me"
        if path.startswith(f"{personal_root}/"):
            return f"/me{path.removeprefix(personal_root)}"
    return path


def to_file_response(file_entity, current_user_id: UUID) -> FileResponse:
    response = FileResponse.model_validate(file_entity)
    response.path = to_public_file_path(
        file_entity.path,
        current_user_id=current_user_id,
        owner_user_id=file_entity.owner_user_id,
    )
    return response


def ensure_file_in_pod(file_entity: FileResponse, pod_id: UUID) -> None:
    if file_entity.pod_id != pod_id:
        raise DatastoreValidationError("File does not belong to this pod")
