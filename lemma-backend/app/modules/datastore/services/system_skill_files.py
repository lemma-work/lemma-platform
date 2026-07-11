from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    FileKind,
    FileStatus,
)
from app.modules.datastore.services.files.paths import (
    normalize_datastore_name,
    normalize_datastore_path,
)

SYSTEM_SKILLS_ROOT_PATH = "/skills"
SYSTEM_SKILLS_METADATA_SOURCE = "system_skill"


def _repo_root_for_system_skills() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        skills_root = parent / "lemma-skills"
        if skills_root.is_dir():
            return parent
    return None


class SystemSkillFileProvider:
    root_path = SYSTEM_SKILLS_ROOT_PATH

    def __init__(self, skills_root: Path | None = None):
        self.skills_root = skills_root

    def is_path(self, path: str) -> bool:
        normalized = self._normalize_path(path)
        return normalized == self.root_path or normalized.startswith(
            f"{self.root_path}/"
        )

    def ensure_writable(self, path: str) -> None:
        normalized = self._normalize_path(path)
        if not self.is_path(normalized):
            return
        parts = self._path_parts(normalized)
        if not parts or parts[0] in self._skill_directories():
            raise DatastoreValidationError("System skills are read-only")

    def get_entity(self, pod_id: UUID, path: str) -> DatastoreFileEntity | None:
        normalized = self._normalize_path(path)
        if normalized == self.root_path:
            return self._root_entity(pod_id)

        source_path = self.source_path(normalized)
        if source_path is None:
            return None
        return self._entity(
            pod_id=pod_id,
            path=normalized,
            name=source_path.name,
            kind=FileKind.FOLDER if source_path.is_dir() else FileKind.FILE,
            source_path=source_path,
        )

    def source_path(self, path: str) -> Path | None:
        normalized = self._normalize_path(path)
        if not self.is_path(normalized):
            return None
        parts = self._path_parts(normalized)
        if not parts:
            return self._skills_root()

        skill_dir = self._skill_directories().get(parts[0])
        if skill_dir is None:
            return None
        candidate = (skill_dir / Path(*parts[1:])).resolve()
        try:
            candidate.relative_to(skill_dir.resolve())
        except ValueError:
            return None
        if not candidate.exists():
            return None
        return candidate

    def read_file(self, path: str) -> bytes | None:
        source_path = self.source_path(path)
        if source_path is None or not source_path.is_file():
            return None
        return source_path.read_bytes()

    def list_direct_children(
        self,
        pod_id: UUID,
        directory_path: str,
    ) -> list[DatastoreFileEntity]:
        normalized_directory = self._normalize_path(directory_path)
        if normalized_directory == self.root_path:
            return [
                self._entity(
                    pod_id=pod_id,
                    path=f"{self.root_path}/{name}",
                    name=name,
                    kind=FileKind.FOLDER,
                    source_path=source_path,
                )
                for name, source_path in self._skill_directories().items()
            ]

        source_directory = self.source_path(normalized_directory)
        if source_directory is None or not source_directory.is_dir():
            return []

        children: list[DatastoreFileEntity] = []
        for child in sorted(
            source_directory.iterdir(), key=lambda path: path.name.lower()
        ):
            if child.name.startswith("."):
                continue
            children.append(
                self._entity(
                    pod_id=pod_id,
                    path=f"{normalized_directory}/{child.name}",
                    name=child.name,
                    kind=FileKind.FOLDER if child.is_dir() else FileKind.FILE,
                    source_path=child,
                )
            )
        return children

    def all_entities(self, pod_id: UUID) -> list[DatastoreFileEntity]:
        entities = [self._root_entity(pod_id)]
        for skill_name, skill_dir in self._skill_directories().items():
            skill_path = f"{self.root_path}/{skill_name}"
            entities.append(
                self._entity(
                    pod_id=pod_id,
                    path=skill_path,
                    name=skill_name,
                    kind=FileKind.FOLDER,
                    source_path=skill_dir,
                )
            )
            for child in sorted(
                skill_dir.rglob("*"), key=lambda path: str(path).lower()
            ):
                if any(
                    part.startswith(".") for part in child.relative_to(skill_dir).parts
                ):
                    continue
                relative = child.relative_to(skill_dir)
                entities.append(
                    self._entity(
                        pod_id=pod_id,
                        path=f"{skill_path}/{relative.as_posix()}",
                        name=child.name,
                        kind=FileKind.FOLDER if child.is_dir() else FileKind.FILE,
                        source_path=child,
                    )
                )
        return entities

    def _skills_root(self) -> Path:
        if self.skills_root is not None:
            return self.skills_root
        repo_root = _repo_root_for_system_skills()
        if repo_root is not None:
            return repo_root / "lemma-skills"
        # No bundled skills directory (e.g. a deployment that ships without
        # lemma-skills). Point at a path that does not exist so the overlay is
        # simply empty — `/skills` still exists as a folder users can populate.
        return Path(__file__).resolve().parent / "lemma-skills"

    def _skill_directories(self) -> dict[str, Path]:
        skills_root = self._skills_root()
        if not skills_root.is_dir():
            return {}
        return {
            item.name: item
            for item in sorted(
                skills_root.iterdir(), key=lambda path: path.name.lower()
            )
            if item.is_dir() and not item.name.startswith(".")
        }

    def root_folder_entity(self, pod_id: UUID) -> DatastoreFileEntity:
        """The synthetic ``/skills`` folder entity.

        Always available (even when no skills directory is bundled) so the root
        file listing/tree can surface ``/skills`` by default — an empty folder
        users and agents can drop a ``<skill>/skill.md`` into.
        """
        return self._root_entity(pod_id)

    def _root_entity(self, pod_id: UUID) -> DatastoreFileEntity:
        skills_root = self._skills_root()
        return self._entity(
            pod_id=pod_id,
            path=self.root_path,
            name="skills",
            kind=FileKind.FOLDER,
            source_path=skills_root if skills_root.is_dir() else None,
        )

    def _entity(
        self,
        *,
        pod_id: UUID,
        path: str,
        name: str,
        kind: FileKind,
        source_path: Path | None = None,
    ) -> DatastoreFileEntity:
        from app.core.file_types import get_content_type

        stat = None
        if source_path is not None:
            try:
                stat = source_path.stat()
            except OSError:
                stat = None
        timestamp = (
            datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            if stat is not None
            else datetime.now(timezone.utc)
        )
        mime_type = (
            "application/x-directory"
            if kind == FileKind.FOLDER
            else get_content_type(name)
        )
        return DatastoreFileEntity(
            id=uuid5(
                NAMESPACE_URL, f"lemma:system-skills:{self._normalize_path(path)}"
            ),
            pod_id=pod_id,
            owner_user_id=None,
            kind=kind,
            visibility="POD",
            path=self._normalize_path(path),
            name=name,
            description="Built-in system skill" if kind == FileKind.FOLDER else None,
            mime_type=mime_type,
            size_bytes=stat.st_size
            if stat is not None and kind == FileKind.FILE
            else 0,
            search_enabled=False,
            status=FileStatus.NOT_REQUIRED,
            metadata={"source": SYSTEM_SKILLS_METADATA_SOURCE, "read_only": True},
            created_at=timestamp,
            updated_at=timestamp,
        )

    def _path_parts(self, path: str) -> list[str]:
        normalized = self._normalize_path(path)
        if normalized == self.root_path:
            return []
        return [
            part
            for part in normalized.removeprefix(f"{self.root_path}/").split("/")
            if part
        ]

    def _normalize_name(self, name: str) -> str:
        return normalize_datastore_name(name)

    def _normalize_path(self, path: str | None) -> str:
        return normalize_datastore_path(path)
