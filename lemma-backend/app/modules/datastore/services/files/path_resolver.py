from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from app.core.authorization.context import ResourceVisibility
from app.modules.datastore.domain.errors import (
    DatastoreAccessDeniedError,
    DatastoreValidationError,
)
from app.modules.datastore.domain.file_entities import DatastoreFileEntity, FileKind, FileStatus
from app.modules.datastore.services.files.paths import (
    normalize_datastore_name,
    normalize_datastore_path,
)


class PathResolver:
    """Pure path/name policy: normalization, ``/me`` translation, visibility
    resolution, and the synthetic personal-root folder. Holds no I/O deps."""

    def _personal_root_folder_id(self, pod_id: UUID, requester_user_id: UUID) -> UUID:
        return uuid5(
            NAMESPACE_URL,
            f"lemma:datastore:personal-root:{pod_id}:{requester_user_id}",
        )

    def _personal_root_folder_entity(
        self,
        pod_id: UUID,
        requester_user_id: UUID,
    ) -> DatastoreFileEntity:
        return DatastoreFileEntity(
            id=self._personal_root_folder_id(pod_id, requester_user_id),
            pod_id=pod_id,
            owner_user_id=requester_user_id,
            kind=FileKind.FOLDER,
            visibility=ResourceVisibility.PERSONAL.value,
            path=self._personal_root_path(requester_user_id),
            name="me",
            description=None,
            mime_type="application/x-directory",
            size_bytes=0,
            search_enabled=False,
            status=FileStatus.NOT_REQUIRED,
        )

    def _should_include_personal_root_folder(
        self,
        *,
        requested_directory_path: str,
        requester_user_id: UUID,
    ) -> bool:
        return requested_directory_path in {
            "/",
            self._personal_root_path(requester_user_id),
        }

    def _should_sync_projections(
        self,
        datastore_search_enabled: bool,
        file_entity: DatastoreFileEntity,
        *,
        previous_search_enabled: bool | None = None,
    ) -> bool:
        if not file_entity.is_file:
            return False
        if not datastore_search_enabled:
            return False
        if (
            previous_search_enabled is not None
            and not previous_search_enabled
            and not file_entity.search_enabled
        ):
            return False
        return file_entity.search_enabled

    def _get_content_type(self, file_name: str) -> str:
        from app.modules.agent.domain.file_entities import get_content_type

        return get_content_type(file_name)

    def _default_visibility_for_path(
        self,
        path: str,
        requester_user_id: UUID,
    ) -> str:
        if self._is_requester_personal_path(path, requester_user_id):
            return ResourceVisibility.PERSONAL.value
        return ResourceVisibility.POD.value

    def _resolve_visibility_for_path(
        self,
        path: str,
        requester_user_id: UUID,
        visibility: str | ResourceVisibility | None,
    ) -> str:
        if visibility is None:
            return self._default_visibility_for_path(path, requester_user_id)
        raw = visibility.value if isinstance(visibility, ResourceVisibility) else str(visibility)
        try:
            normalized = ResourceVisibility(raw.upper())
        except ValueError as exc:
            allowed = [v.value for v in ResourceVisibility]
            raise DatastoreValidationError(
                f"Unsupported file visibility '{visibility}'. Allowed values: {', '.join(allowed)}",
                details={"value": visibility, "allowed_values": allowed},
            ) from exc
        return normalized.value

    def _personal_root_path(self, requester_user_id: UUID) -> str:
        return f"/{requester_user_id}"

    def _is_personal_root_path(self, path: str) -> bool:
        normalized = self._normalize_path(path)
        if normalized == "/":
            return False
        segments = [segment for segment in normalized.split("/") if segment]
        if len(segments) != 1:
            return False
        try:
            UUID(segments[0])
        except ValueError:
            return False
        return True

    def _is_requester_personal_path(self, path: str | None, requester_user_id: UUID) -> bool:
        normalized = self._normalize_path(path)
        personal_root = self._personal_root_path(requester_user_id)
        return normalized == personal_root or normalized.startswith(f"{personal_root}/")

    def _is_personal_file(self, file_entity: DatastoreFileEntity) -> bool:
        return file_entity.visibility == ResourceVisibility.PERSONAL.value

    def _starts_with_uuid_path_segment(self, path: str) -> bool:
        normalized = self._normalize_path(path)
        segments = [segment for segment in normalized.split("/") if segment]
        if not segments:
            return False
        try:
            UUID(segments[0])
        except ValueError:
            return False
        return True

    def _resolve_api_path(
        self,
        path: str | None,
        *,
        requester_user_id: UUID,
    ) -> str:
        normalized = self._normalize_path(path)
        if normalized == "/me":
            return self._personal_root_path(requester_user_id)
        if normalized.startswith("/me/"):
            return f"{self._personal_root_path(requester_user_id)}{normalized.removeprefix('/me')}"
        return normalized

    def _to_api_path(
        self,
        path: str | None,
        *,
        requester_user_id: UUID,
    ) -> str:
        normalized = self._normalize_path(path)
        personal_root = self._personal_root_path(requester_user_id)
        if normalized == personal_root:
            return "/me"
        if normalized.startswith(f"{personal_root}/"):
            return f"/me{normalized.removeprefix(personal_root)}"
        return normalized

    def _ensure_personal_write_path(
        self,
        *,
        path: str,
        requester_user_id: UUID,
    ) -> None:
        if not self._starts_with_uuid_path_segment(path):
            return
        if self._is_requester_personal_path(path, requester_user_id):
            return
        raise DatastoreAccessDeniedError("Personal files must be written under /me")

    def _normalize_name(self, name: str) -> str:
        return normalize_datastore_name(name)

    def _normalize_path(self, path: str | None) -> str:
        return normalize_datastore_path(path)

    def _split_parent_path(self, path: str) -> tuple[str, str]:
        normalized = self._normalize_path(path)
        if normalized == "/":
            raise DatastoreValidationError("Root path has no parent")
        parent_path, _, name = normalized.rpartition("/")
        return parent_path or "/", name

    def _join_child_path(self, directory_path: str, name: str) -> str:
        normalized_directory = self._normalize_path(directory_path)
        normalized_name = self._normalize_name(name)
        if normalized_directory == "/":
            return f"/{normalized_name}"
        return f"{normalized_directory}/{normalized_name}"

    def _parent_path(self, path: str) -> str:
        normalized = self._normalize_path(path)
        if normalized == "/":
            return "/"
        parent, _, _ = normalized.rpartition("/")
        return parent or "/"

    def ancestor_paths(self, path: str) -> list[str]:
        """Return ``path`` plus every ancestor up to the root, in descent order."""
        paths: list[str] = []
        current_path = path
        while True:
            paths.append(current_path)
            parent_path = self._parent_path(current_path)
            if parent_path == current_path:
                break
            current_path = parent_path
        return paths
