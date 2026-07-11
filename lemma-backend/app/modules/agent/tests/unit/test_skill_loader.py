from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.tools.skills import skill_loader
from app.modules.agent.tools.skills.skill_loader import (
    list_workspace_skill_resources,
    list_workspace_skills,
    read_workspace_skill,
    read_workspace_skill_resource,
)
from app.modules.agent.tools.skills import pydantic_adapter as skills_adapter
from app.modules.agent.tools.skills.models import SkillLookupRequest
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.datastore.domain.errors import DatastoreFileNotFoundError


@dataclass(frozen=True)
class _FakeFileEntity:
    path: str
    name: str
    kind: str

    @property
    def is_folder(self) -> bool:
        return self.kind == "FOLDER"

    @property
    def is_file(self) -> bool:
        return self.kind == "FILE"


class _FakeSkillFileService:
    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.list_contexts = []
        self.download_contexts = []
        folders = {"/skills"}
        for file_path in files:
            parts = file_path.strip("/").split("/")
            for index in range(1, len(parts)):
                folders.add(f"/{'/'.join(parts[:index])}")
        self.entities = {
            path: _FakeFileEntity(
                path=path, name=path.rsplit("/", 1)[-1], kind="FOLDER"
            )
            for path in folders
        }
        self.entities.update(
            {
                path: _FakeFileEntity(
                    path=path, name=path.rsplit("/", 1)[-1], kind="FILE"
                )
                for path in files
            }
        )

    async def list_files(
        self,
        *,
        pod_id,
        ctx=None,
        directory_path: str,
        limit: int,
        cursor: str | None,
    ):
        del pod_id, limit, cursor
        self.list_contexts.append(ctx)
        parent = directory_path.rstrip("/") or "/"
        children = [
            entity
            for entity in self.entities.values()
            if entity.path != parent and entity.path.rsplit("/", 1)[0] == parent
        ]
        children.sort(key=lambda item: (not item.is_folder, item.name))
        return children, None

    async def download_file_content_by_path(
        self,
        *,
        pod_id,
        path: str,
        ctx=None,
    ):
        del pod_id
        self.download_contexts.append(ctx)
        content = self.files.get(path)
        if content is None:
            raise DatastoreFileNotFoundError(f"File {path} not found")
        return self.entities[path], content


def _skill_md(name: str, description: str) -> bytes:
    return f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n".encode()


@pytest.mark.asyncio
async def test_pod_skill_loader_lists_system_and_custom_skills_with_skill_md():
    service = _FakeSkillFileService(
        {
            "/skills/browser/SKILL.md": _skill_md("browser", "Browser skill"),
            "/skills/custom-skill/SKILL.md": _skill_md(
                "custom-skill", "Custom pod skill"
            ),
            "/skills/not-a-skill/README.md": b"# Missing skill file\n",
        }
    )

    skills = await list_workspace_skills(
        pod_id=uuid4(),
        user_id=uuid4(),
        file_service=service,
    )

    assert [item["name"] for item in skills] == ["browser", "custom-skill"]
    assert skills[1]["workspace_path"] == "/skills/custom-skill/SKILL.md"
    assert skills[1]["workspace_dir"] == "/skills/custom-skill"


@pytest.mark.asyncio
async def test_pod_skill_loader_reads_skill_content_and_resources():
    service = _FakeSkillFileService(
        {
            "/skills/custom-skill/SKILL.md": _skill_md(
                "custom-skill", "Custom pod skill"
            ),
            "/skills/custom-skill/scripts/setup.sh": b"echo setup\n",
            "/skills/custom-skill/references/example.md": b"# Example\n",
        }
    )
    pod_id = uuid4()
    user_id = uuid4()

    content = await read_workspace_skill(
        "custom-skill",
        pod_id=pod_id,
        user_id=user_id,
        file_service=service,
    )
    resources = await list_workspace_skill_resources(
        "custom-skill",
        pod_id=pod_id,
        user_id=user_id,
        file_service=service,
    )
    script = await read_workspace_skill_resource(
        "custom-skill",
        "scripts/setup.sh",
        pod_id=pod_id,
        user_id=user_id,
        file_service=service,
    )

    assert "name: custom-skill" in content
    assert resources == [
        {
            "path": "references/example.md",
            "workspace_path": "/skills/custom-skill/references/example.md",
            "kind": "text",
            "executable": "false",
        },
        {
            "path": "scripts/setup.sh",
            "workspace_path": "/skills/custom-skill/scripts/setup.sh",
            "kind": "script",
            "executable": "true",
        },
    ]
    assert script == "echo setup\n"


@pytest.mark.asyncio
async def test_system_widget_skill_exposes_versioned_starter_assets():
    resources = await list_workspace_skill_resources("lemma-widget")

    assert {item["path"] for item in resources} >= {
        "assets/widget-starter-v1.html",
        "assets/widget-list-v1.html",
        "assets/widget-chart-v1.html",
        "assets/widget-detail-v1.html",
    }
    starter = await read_workspace_skill_resource(
        "lemma-widget", "assets/widget-starter-v1.html"
    )
    assert 'data-lemma-widget-version="1"' in starter
    assert "window.__LEMMA_CONFIG__" in starter


@pytest.mark.asyncio
async def test_pod_skill_loader_passes_authz_context_for_datastore_service(
    monkeypatch: pytest.MonkeyPatch,
):
    authz_ctx = object()

    class _FakeAuthorizationDataService:
        def __init__(self, session):
            self.session = session

        async def build_user_context(self, *, user_id, pod_id):
            del user_id, pod_id
            return authz_ctx

    service = _FakeSkillFileService(
        {
            "/skills/custom-skill/SKILL.md": _skill_md(
                "custom-skill", "Custom pod skill"
            ),
        }
    )
    service.file_repository = SimpleNamespace(session=object())
    monkeypatch.setattr(
        skill_loader,
        "AuthorizationDataService",
        _FakeAuthorizationDataService,
    )

    skills = await list_workspace_skills(
        pod_id=uuid4(),
        user_id=uuid4(),
        file_service=service,
    )

    assert [item["name"] for item in skills] == ["custom-skill"]
    assert service.list_contexts == [authz_ctx]
    assert service.download_contexts == [authz_ctx]


@pytest.mark.asyncio
async def test_load_skill_appends_local_workspace_override(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_read_workspace_skill(name, *, pod_id, user_id):
        del pod_id, user_id
        return f"# {name}\nRun `lemma pods create demo`.\n"

    monkeypatch.setattr(
        skills_adapter,
        "read_workspace_skill",
        fake_read_workspace_skill,
    )
    ctx = SimpleNamespace(
        deps=BaseAgentContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
        )
    )

    result = await skills_adapter.load_skill(
        ctx,
        SkillLookupRequest(name="lemma-builder"),
    )

    assert result.success is True
    assert result.content is not None
    assert "Run `lemma pods create demo`." in result.content
    assert "Local Lemma Workspace Override" in result.content
    assert "run CLI examples through `lemma_exec_command`" in result.content


@pytest.mark.asyncio
async def test_skill_download_releases_uow_before_storage_read():
    order: list[str] = []

    class _UoW:
        async def commit(self):
            order.append("commit")

    class _TwoPhaseFileService:
        file_repository = SimpleNamespace(uow=_UoW())

        async def resolve_readable_file(self, pod_id, path, ctx):
            del pod_id, path, ctx
            order.append("resolve")
            return object()

        async def read_file_content(self, entity):
            del entity
            order.append("read")
            return b"content"

    content = await skill_loader._download_text_file(
        _TwoPhaseFileService(),
        pod_id=uuid4(),
        user_id=uuid4(),
        path="/skills/example/SKILL.md",
        ctx=object(),  # type: ignore[arg-type]
    )

    assert content == "content"
    assert order == ["resolve", "commit", "read"]
