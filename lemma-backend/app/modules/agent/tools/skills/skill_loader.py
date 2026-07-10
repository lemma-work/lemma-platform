from __future__ import annotations

import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from app.core.authorization.context import Context
from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.datastore.contracts import DatastoreFileNotFoundError
from app.composition.agent_datastore import create_agent_skill_file_service
from app.composition.authorization import create_authorization_service

_FRONTMATTER_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_SKILLS_ROOT = "/skills"


@dataclass(frozen=True)
class SkillEntry:
    name: str
    description: str
    path: str
    workspace_path: str
    workspace_dir: str


@dataclass(frozen=True)
class _FileServiceScope:
    service: Any
    ctx: Context | None


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        skills_dir = parent / "lemma-skills"
        if skills_dir.is_dir() and any(skills_dir.glob("*/SKILL.md")):
            return parent
    raise RuntimeError("Could not locate repository root for skills loading")


def _skills_root() -> Path:
    root = _repo_root() / "lemma-skills"
    if not root.exists() or not root.is_dir():
        raise RuntimeError(f"Skills directory not found: {root}")
    return root


def _skill_dir_path(name: str) -> str:
    return f"{_SKILLS_ROOT}/{name}"


def _skill_file_path(name: str, relative: Path | str = "SKILL.md") -> str:
    relative_path = Path(relative).as_posix().lstrip("/")
    return f"{_skill_dir_path(name)}/{relative_path}"


def _resource_kind(path: Path) -> str:
    if "scripts" in path.parts:
        return "script"
    if path.suffix.lower() in {".md", ".txt", ".json", ".yaml", ".yml"}:
        return "text"
    return "file"


def _parse_frontmatter(skill_md: Path, content: str) -> tuple[str, str]:
    if not content.startswith("---\n"):
        raise ValueError(
            f"{skill_md} must start with YAML frontmatter delimited by '---'"
        )

    lines = content.splitlines()
    end_idx: int | None = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break

    if end_idx is None:
        raise ValueError(f"{skill_md} frontmatter is not closed with '---'")

    frontmatter: dict[str, str] = {}
    for raw in lines[1:end_idx]:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if raw.startswith((" ", "\t")):
            continue
        if ":" not in line:
            continue

        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            frontmatter[key] = value

    name = frontmatter.get("name")
    description = frontmatter.get("description")

    if not name:
        raise ValueError(f"{skill_md} is missing required frontmatter field 'name'")
    if not description:
        raise ValueError(
            f"{skill_md} is missing required frontmatter field 'description'"
        )

    if not _FRONTMATTER_NAME_PATTERN.match(name) or "--" in name:
        raise ValueError(
            f"{skill_md} has invalid skill name '{name}'. Expected lowercase letters, numbers, and hyphens only."
        )

    if skill_md.parent.name != name:
        raise ValueError(
            f"{skill_md} has name '{name}' but directory is '{skill_md.parent.name}'. They must match."
        )

    return name, description


def _build_system_skill_catalog() -> dict[str, SkillEntry]:
    skills_root = _skills_root()
    entries: dict[str, SkillEntry] = {}
    repo_root = _repo_root()

    for skill_md in sorted(skills_root.glob("*/SKILL.md")):
        content = skill_md.read_text(encoding="utf-8")
        name, description = _parse_frontmatter(skill_md, content)

        if name in entries:
            raise ValueError(f"Duplicate skill name '{name}' found in {skill_md}")

        entries[name] = SkillEntry(
            name=name,
            description=description,
            path=str(skill_md.relative_to(repo_root)),
            workspace_path=_skill_file_path(name),
            workspace_dir=_skill_dir_path(name),
        )

    return entries


def _get_system_skill(name: str) -> SkillEntry:
    catalog = _build_system_skill_catalog()
    entry = catalog.get(name)
    if entry is None:
        available = ", ".join(sorted(catalog.keys()))
        raise ValueError(f"Unknown skill '{name}'. Available: {available}")
    return entry


async def _build_authz_context_from_service(
    file_service: Any,
    *,
    pod_id: UUID,
    user_id: UUID,
) -> Context | None:
    repository = getattr(file_service, "file_repository", None)
    session = getattr(repository, "session", None)
    if session is None:
        return None
    return await AuthorizationDataService(session).build_user_context(
        user_id=user_id,
        pod_id=pod_id,
    )


@asynccontextmanager
async def _default_file_service(
    *,
    pod_id: UUID,
    user_id: UUID,
) -> AsyncIterator[_FileServiceScope]:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        service = create_agent_skill_file_service(
            uow,
            authorization_service=create_authorization_service(uow),
        )
        ctx = await AuthorizationDataService(uow.session).build_user_context(
            user_id=user_id,
            pod_id=pod_id,
        )
        yield _FileServiceScope(service=service, ctx=ctx)


@asynccontextmanager
async def _file_service_context(
    file_service: Any | None,
    *,
    pod_id: UUID,
    user_id: UUID,
) -> AsyncIterator[_FileServiceScope]:
    if file_service is not None:
        ctx = await _build_authz_context_from_service(
            file_service,
            pod_id=pod_id,
            user_id=user_id,
        )
        yield _FileServiceScope(service=file_service, ctx=ctx)
        return

    async with _default_file_service(pod_id=pod_id, user_id=user_id) as scope:
        yield scope


async def _list_all_files(
    file_service: Any,
    *,
    pod_id: UUID,
    user_id: UUID,
    directory_path: str,
    ctx: Context | None,
) -> list[Any]:
    items: list[Any] = []
    cursor: str | None = None
    while True:
        page, cursor = await file_service.list_files(
            pod_id=pod_id,
            ctx=ctx,
            directory_path=directory_path,
            limit=1000,
            cursor=cursor,
        )
        items.extend(page)
        if not cursor:
            return items


async def _download_text_file(
    file_service: Any,
    *,
    pod_id: UUID,
    user_id: UUID,
    path: str,
    ctx: Context | None,
) -> str:
    resolve_file = getattr(file_service, "resolve_readable_file", None)
    read_content = getattr(file_service, "read_file_content", None)
    if ctx is not None and callable(resolve_file) and callable(read_content):
        resolve = cast(Callable[[UUID, str, Context], Awaitable[Any]], resolve_file)
        read = cast(Callable[[Any], Awaitable[bytes]], read_content)
        file_entity = await resolve(pod_id, path, ctx)
        repository = getattr(file_service, "file_repository", None)
        uow = getattr(repository, "uow", None)
        if uow is not None:
            # Release the pooled DB connection before object-storage I/O. A
            # later metadata call can safely autobegin a fresh short transaction.
            commit = cast(Callable[[], Awaitable[None]], uow.commit)
            await commit()
        content = await read(file_entity)
    else:
        _, content = await file_service.download_file_content_by_path(
            pod_id=pod_id,
            path=path,
            ctx=ctx,
        )
    return content.decode("utf-8")


async def _build_pod_skill_catalog(
    *,
    pod_id: UUID,
    user_id: UUID,
    file_service: Any | None = None,
) -> dict[str, SkillEntry]:
    entries: dict[str, SkillEntry] = {}
    async with _file_service_context(
        file_service,
        pod_id=pod_id,
        user_id=user_id,
    ) as scope:
        skill_folders = await _list_all_files(
            scope.service,
            pod_id=pod_id,
            user_id=user_id,
            directory_path=_SKILLS_ROOT,
            ctx=scope.ctx,
        )
        for folder in skill_folders:
            if not getattr(folder, "is_folder", False):
                continue

            skill_md_path = _skill_file_path(folder.name)
            try:
                content = await _download_text_file(
                    scope.service,
                    pod_id=pod_id,
                    user_id=user_id,
                    path=skill_md_path,
                    ctx=scope.ctx,
                )
            except (DatastoreFileNotFoundError, FileNotFoundError, UnicodeDecodeError):
                continue

            try:
                name, description = _parse_frontmatter(Path(skill_md_path), content)
            except ValueError:
                continue
            if name in entries:
                raise ValueError(f"Duplicate skill name '{name}' found in /skills")
            entries[name] = SkillEntry(
                name=name,
                description=description,
                path=skill_md_path,
                workspace_path=skill_md_path,
                workspace_dir=_skill_dir_path(name),
            )
    return entries


async def _build_skill_catalog(
    *,
    pod_id: UUID | None = None,
    user_id: UUID | None = None,
    file_service: Any | None = None,
) -> dict[str, SkillEntry]:
    if pod_id is None or user_id is None:
        return _build_system_skill_catalog()
    return await _build_pod_skill_catalog(
        pod_id=pod_id,
        user_id=user_id,
        file_service=file_service,
    )


async def _get_skill(
    name: str,
    *,
    pod_id: UUID | None = None,
    user_id: UUID | None = None,
    file_service: Any | None = None,
) -> SkillEntry:
    catalog = await _build_skill_catalog(
        pod_id=pod_id,
        user_id=user_id,
        file_service=file_service,
    )
    entry = catalog.get(name)
    if entry is None:
        available = ", ".join(sorted(catalog.keys()))
        raise ValueError(f"Unknown skill '{name}'. Available: {available}")
    return entry


async def list_workspace_skills(
    *,
    pod_id: UUID | None = None,
    user_id: UUID | None = None,
    file_service: Any | None = None,
) -> list[dict[str, str]]:
    items = []
    catalog = await _build_skill_catalog(
        pod_id=pod_id,
        user_id=user_id,
        file_service=file_service,
    )
    for entry in sorted(catalog.values(), key=lambda item: item.name):
        items.append(
            {
                "name": entry.name,
                "description": entry.description,
                "path": entry.path,
                "workspace_path": entry.workspace_path,
                "workspace_dir": entry.workspace_dir,
            }
        )
    return items


async def read_workspace_skill(
    name: str,
    *,
    pod_id: UUID | None = None,
    user_id: UUID | None = None,
    file_service: Any | None = None,
) -> str:
    if pod_id is None or user_id is None:
        _get_system_skill(name)
        skill_md = _skills_root() / name / "SKILL.md"
        return skill_md.read_text(encoding="utf-8")

    await _get_skill(
        name,
        pod_id=pod_id,
        user_id=user_id,
        file_service=file_service,
    )
    async with _file_service_context(
        file_service,
        pod_id=pod_id,
        user_id=user_id,
    ) as scope:
        return await _download_text_file(
            scope.service,
            pod_id=pod_id,
            user_id=user_id,
            path=_skill_file_path(name),
            ctx=scope.ctx,
        )


async def list_workspace_skill_resources(
    name: str,
    *,
    pod_id: UUID | None = None,
    user_id: UUID | None = None,
    file_service: Any | None = None,
) -> list[dict[str, str]]:
    if pod_id is None or user_id is None:
        return _list_system_skill_resources(name)

    await _get_skill(
        name,
        pod_id=pod_id,
        user_id=user_id,
        file_service=file_service,
    )
    resources: list[dict[str, str]] = []
    async with _file_service_context(
        file_service,
        pod_id=pod_id,
        user_id=user_id,
    ) as scope:
        pending = [_skill_dir_path(name)]
        while pending:
            directory = pending.pop(0)
            for item in await _list_all_files(
                scope.service,
                pod_id=pod_id,
                user_id=user_id,
                directory_path=directory,
                ctx=scope.ctx,
            ):
                relative_path = item.path.removeprefix(f"{_skill_dir_path(name)}/")
                relative = Path(relative_path)
                if "__pycache__" in relative.parts:
                    continue
                if getattr(item, "is_folder", False):
                    pending.append(item.path)
                    continue
                if item.name == "SKILL.md":
                    continue
                resources.append(
                    {
                        "path": relative.as_posix(),
                        "workspace_path": item.path,
                        "kind": _resource_kind(relative),
                        "executable": "true" if relative.suffix == ".sh" else "false",
                    }
                )
    resources.sort(key=lambda item: item["path"])
    return resources


def _list_system_skill_resources(name: str) -> list[dict[str, str]]:
    _get_system_skill(name)
    skill_dir = _skills_root() / name

    resources: list[dict[str, str]] = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "SKILL.md":
            continue
        if "__pycache__" in path.parts:
            continue

        relative = path.relative_to(skill_dir)
        resources.append(
            {
                "path": str(relative),
                "workspace_path": _skill_file_path(name, relative),
                "kind": _resource_kind(relative),
                "executable": "true" if path.suffix == ".sh" else "false",
            }
        )

    return resources


async def read_workspace_skill_resource(
    name: str,
    resource_path: str,
    *,
    pod_id: UUID | None = None,
    user_id: UUID | None = None,
    file_service: Any | None = None,
) -> str:
    relative = Path(resource_path)
    if relative.is_absolute():
        raise ValueError("resource_path must be relative to the skill directory")
    if any(part == ".." for part in relative.parts):
        raise ValueError(
            "resource_path must point to a file inside the skill directory"
        )

    if pod_id is None or user_id is None:
        return _read_system_skill_resource(name, relative)

    await _get_skill(
        name,
        pod_id=pod_id,
        user_id=user_id,
        file_service=file_service,
    )
    async with _file_service_context(
        file_service,
        pod_id=pod_id,
        user_id=user_id,
    ) as scope:
        return await _download_text_file(
            scope.service,
            pod_id=pod_id,
            user_id=user_id,
            path=_skill_file_path(name, relative),
            ctx=scope.ctx,
        )


def _read_system_skill_resource(name: str, relative: Path) -> str:
    _get_system_skill(name)
    skill_root = (_skills_root() / name).resolve()
    candidate = (skill_root / relative).resolve()

    try:
        candidate.relative_to(skill_root)
    except ValueError as exc:
        raise ValueError(
            "resource_path must point to a file inside the skill directory"
        ) from exc

    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError(
            f"Skill resource not found for '{name}': {relative.as_posix()}"
        )

    return candidate.read_text(encoding="utf-8")
