"""E2E: the pod toolset enforces agent grants against the real datastore.

Drives the in-process pod tools (pod_write_record / pod_get_records) with an
agent run context and asserts the grant model end-to-end: the pod default
assistant works with the user's permissions; a named agent is denied (and told
to request approval) until granted datastore.record.write, after which the write
goes through.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.authorization.delegation import is_pod_default_agent
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.infrastructure.tools.pod.models import (
    PodGetRecordsRequest,
    PodListFilesRequest,
    PodReadFileRequest,
    PodTablesRequest,
    PodWriteFileRequest,
    PodWriteRecordRequest,
    RecordFilter,
)
from app.modules.agent.infrastructure.tools.pod.pod_file_tools import (
    pod_list_files,
    pod_read_file,
    pod_write_file,
)
from app.modules.agent.infrastructure.tools.pod.tools import (
    pod_get_records,
    pod_tables,
    pod_write_record,
)

pytestmark = pytest.mark.e2e


async def _create_pod(authenticated_client, fixed_test_org) -> str:
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"pod-tools-{uuid4().hex[:8]}",
            "description": "Pod toolset e2e",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _create_table(authenticated_client, pod_id: str, table_name: str) -> None:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": table_name,
            "primary_key_column": "id",
            "enable_rls": False,
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "title", "type": "TEXT", "required": True},
            ],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text


async def _create_table_all_optional(
    authenticated_client, pod_id: str, table_name: str
) -> None:
    """A table whose only writable column is optional — so an empty payload would
    pass record validation and (before the fix) silently write a blank row."""
    response = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": table_name,
            "primary_key_column": "id",
            "enable_rls": False,
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "note", "type": "TEXT", "required": False},
            ],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text


async def _create_agent(authenticated_client, pod_id: str, name: str) -> dict:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": name, "instruction": "Answer briefly."},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _grant(authenticated_client, pod_id, agent_name, table_name) -> None:
    response = await authenticated_client.put(
        f"/pods/{pod_id}/agents/{agent_name}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "agent",
                    "resource_name": agent_name,
                    "permission_ids": ["agent.read"],
                },
                {
                    "resource_type": "datastore_table",
                    "resource_name": table_name,
                    "permission_ids": [
                        "datastore.table.read",
                        "datastore.record.read",
                        "datastore.record.write",
                    ],
                },
            ]
        },
    )
    assert response.status_code == status.HTTP_200_OK, response.text


def _run_ctx(*, user_id, pod_id, workload_id, agent_name):
    """A run context shaped like ``run_context_builder`` builds one.

    The assistant is addressed by its ``agents`` row id, which is its pod's, so
    callers pass ``workload_id=pod_id`` for it and a real agent id otherwise --
    and ``is_pod_default_agent`` follows from that rather than from the name.
    """
    resolved_workload_id = UUID(workload_id) if workload_id is not None else None
    return ToolContext(
        user_id=UUID(user_id),
        pod_id=UUID(pod_id),
        conversation_id=uuid4(),
        workload_type="agent" if workload_id is not None else None,
        workload_id=resolved_workload_id,
        agent_name=agent_name,
        is_pod_default_agent=is_pod_default_agent(
            resolved_workload_id, pod_id=UUID(pod_id)
        ),
    )


def _probe_png_bytes() -> bytes:
    """A PNG a human (or a vision model) can identify unambiguously."""
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (480, 200), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 479, 199], outline="black", width=4)
    draw.ellipse([30, 60, 130, 160], fill="red", outline="black", width=3)
    draw.text((160, 90), "LEMMA VISION 4726", fill="black")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_view_image_reads_a_pod_image_intact_without_leaking_bytes(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    """End to end: an image uploaded to a pod is read by `view_image`, and the
    real MCP bridge hands the harness an intact image on the image channel with
    no bytes spilled into the text channel — the critical `view_image` defect.
    """
    import base64
    import io
    import os

    from PIL import Image

    from app.modules.agent.domain.vision import AgentVisionMode
    from app.modules.agent.infrastructure.mcp.content import tool_call_result
    from app.modules.agent.infrastructure.tools.workspace_cli.models import (
        ViewImageRequest,
    )
    from app.modules.agent.infrastructure.tools.workspace_cli.workspace_cli import (
        view_image_internal,
    )

    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    png = _probe_png_bytes()
    upload = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/files",
        data={"directory_path": "/me/vision", "search_enabled": "false"},
        files={"data": ("probe.png", png, "image/png")},
    )
    assert upload.status_code == status.HTTP_201_CREATED, upload.text

    # DIRECT is the desktop configuration: the model IS the harness (Claude) and
    # reads images natively, so `view_image` returns the image inline.
    ctx = ToolContext(
        user_id=UUID(fixed_test_user["id"]),
        pod_id=UUID(pod_id),
        conversation_id=uuid4(),
        workload_type="agent",
        workload_id=UUID(pod_id),
        agent_name="pod_default",
        is_pod_default_agent=True,
        vision_mode=AgentVisionMode.DIRECT,
    )
    tool_return = await view_image_internal(
        ctx, ViewImageRequest(pod_file_path="/me/vision/probe.png")
    )

    # The real serialization the Agent Host consumes.
    result = tool_call_result(tool_return)
    images = [c for c in result.content if getattr(c, "type", None) == "image"]
    texts = [c for c in result.content if getattr(c, "type", None) == "text"]
    assert len(images) == 1, "exactly one image must reach the harness"

    # The bytes are on the image channel and nowhere in the text/structured one.
    text_blob = "".join(c.text for c in texts)
    assert "PNG" not in text_blob and "\\x89" not in text_blob
    assert result.structuredContent.get("success") is True

    # The image is intact: it decodes and keeps the dimensions we uploaded.
    extracted = base64.b64decode(images[0].data)
    restored = Image.open(io.BytesIO(extracted))
    assert restored.size == (480, 200)

    # For human/vision inspection: dump the emergent image when asked.
    dump_dir = os.environ.get("VIEW_IMAGE_DUMP")
    if dump_dir:
        os.makedirs(dump_dir, exist_ok=True)
        out = os.path.join(dump_dir, "view_image_emergent.png")
        with open(out, "wb") as handle:
            handle.write(extracted)
        print(f"VIEW_IMAGE_EMERGENT_PATH={out}")


async def _create_enum_table(
    authenticated_client, pod_id: str, table_name: str
) -> None:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": table_name,
            "primary_key_column": "id",
            "enable_rls": False,
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "title", "type": "TEXT", "required": True},
                {
                    "name": "result",
                    "type": "ENUM",
                    "required": True,
                    "options": ["pass", "fail", "friction"],
                },
                {
                    "name": "summary",
                    "type": "TEXT",
                    "max_length": 500,
                    "default": "pending",
                    "unique": True,
                },
            ],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text


@pytest.mark.asyncio
async def test_pod_get_records_in_operator_matches_any_of_a_list(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    """The agent-facing `in` filter operator returns rows matching any value."""
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    table = f"notes_{uuid4().hex[:8]}"
    await _create_table(authenticated_client, pod_id, table)
    ctx = _run_ctx(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        workload_id=pod_id,
        agent_name="pod_default",
    )
    for title in ("alpha", "beta", "gamma"):
        created = await pod_write_record(
            ctx,
            PodWriteRecordRequest(
                action="create", table_name=table, data={"title": title}
            ),
        )
        assert created["success"] is True, created

    listed = await pod_get_records(
        ctx,
        PodGetRecordsRequest(
            table_name=table,
            filters=[RecordFilter(column="title", op="in", value=["alpha", "gamma"])],
        ),
    )
    assert listed["success"] is True, listed
    assert sorted(r["title"] for r in listed["records"]) == ["alpha", "gamma"]


@pytest.mark.asyncio
async def test_pod_tables_describe_surfaces_enum_options(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    """Describing a table exposes an ENUM column's allowed values."""
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    table = f"audit_{uuid4().hex[:8]}"
    await _create_enum_table(authenticated_client, pod_id, table)
    ctx = _run_ctx(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        workload_id=pod_id,
        agent_name="pod_default",
    )

    described = await pod_tables(ctx, PodTablesRequest(table_name=table))
    assert described["success"] is True, described
    columns = {c["name"]: c for c in described["table"]["columns"]}
    assert columns["result"]["type"] == "ENUM"
    assert columns["result"]["options"] == ["pass", "fail", "friction"]
    # A non-enum column carries no options key.
    assert "options" not in columns["title"]
    # What the database will refuse, said up front: VARCHAR(500) was described
    # as TEXT, so a longer value was only learned from the failed write.
    assert columns["summary"]["sql_type"] == "VARCHAR(500)"
    assert columns["summary"]["max_length"] == 500
    assert columns["summary"]["default"] == "pending"
    assert columns["summary"]["unique"] is True


@pytest.mark.asyncio
async def test_pod_file_tools_report_me_alias_paths(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    """Every pod file tool presents paths in the /me/... alias form."""
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    ctx = _run_ctx(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        workload_id=pod_id,
        agent_name="pod_default",
    )

    written = await pod_write_file(
        ctx,
        PodWriteFileRequest(path="/me/e2e/report.md", content="hello"),
    )
    assert written["success"] is True, written
    assert written["path"] == "/me/e2e/report.md"

    read = await pod_read_file(ctx, PodReadFileRequest(path="/me/e2e/report.md"))
    assert read["success"] is True, read
    assert read["path"] == "/me/e2e/report.md"
    assert read["text"] == "hello"

    listed = await pod_list_files(ctx, PodListFilesRequest(path="/me/e2e"))
    assert listed["success"] is True, listed
    paths = [f["path"] for f in listed["files"]]
    assert "/me/e2e/report.md" in paths
    # Never the raw /{user-uuid}/... storage path.
    assert all(not p.startswith(f"/{fixed_test_user['id']}") for p in paths)


@pytest.mark.asyncio
async def test_pod_default_agent_creates_and_lists_records_with_user_permissions(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    table = f"notes_{uuid4().hex[:8]}"
    await _create_table(authenticated_client, pod_id, table)

    # Pod default assistant: no workload id -> runs with the user's permissions.
    ctx = _run_ctx(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        workload_id=pod_id,
        agent_name="pod_default",
    )

    created = await pod_write_record(
        ctx,
        PodWriteRecordRequest(
            action="create", table_name=table, data={"title": "first"}
        ),
    )
    assert created["success"] is True, created

    listed = await pod_get_records(ctx, PodGetRecordsRequest(table_name=table))
    assert listed["success"] is True
    titles = [r.get("title") for r in listed["records"]]
    assert "first" in titles


@pytest.mark.asyncio
async def test_pod_write_record_rejects_empty_data_and_writes_no_row(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    """Regression: an empty payload must be rejected and persist nothing, even on
    an all-optional table where the write would otherwise create a blank row."""
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    table = f"notes_{uuid4().hex[:8]}"
    await _create_table_all_optional(authenticated_client, pod_id, table)

    ctx = _run_ctx(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        workload_id=pod_id,
        agent_name="pod_default",
    )

    rejected = await pod_write_record(
        ctx, PodWriteRecordRequest(action="create", table_name=table, data={})
    )
    assert rejected["success"] is False
    assert "non-empty" in rejected["error"]

    # Nothing was written.
    listed = await pod_get_records(ctx, PodGetRecordsRequest(table_name=table))
    assert listed["success"] is True
    assert listed["total"] == 0
    assert listed["records"] == []

    # A real payload persists and reads back.
    created = await pod_write_record(
        ctx,
        PodWriteRecordRequest(action="create", table_name=table, data={"note": "hi"}),
    )
    assert created["success"] is True, created

    listed = await pod_get_records(ctx, PodGetRecordsRequest(table_name=table))
    assert listed["total"] == 1
    assert listed["records"][0]["note"] == "hi"


@pytest.mark.asyncio
async def test_named_agent_create_record_gated_by_grant(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    table = f"orders_{uuid4().hex[:8]}"
    await _create_table(authenticated_client, pod_id, table)
    agent_name = f"writer_{uuid4().hex[:8]}"
    agent = await _create_agent(authenticated_client, pod_id, agent_name)

    ctx = _run_ctx(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        workload_id=agent["id"],
        agent_name=agent_name,
    )

    # Without a grant the write is denied and surfaced as needs_approval.
    denied = await pod_write_record(
        ctx,
        PodWriteRecordRequest(
            action="create", table_name=table, data={"title": "blocked"}
        ),
    )
    assert denied["success"] is False
    assert denied["code"] == "MISSING_WORKLOAD_RESOURCE_GRANT"
    assert denied["needs_approval"] is True
    assert denied["approval"]["tool_name"] == "pod_write_record"

    # After granting record.write, the same call succeeds.
    await _grant(authenticated_client, pod_id, agent_name, table)
    allowed = await pod_write_record(
        ctx,
        PodWriteRecordRequest(
            action="create", table_name=table, data={"title": "allowed"}
        ),
    )
    assert allowed["success"] is True, allowed


async def _delete_record(ctx, table: str, record_id: str) -> dict:
    return await pod_write_record(
        ctx,
        PodWriteRecordRequest(action="delete", table_name=table, record_id=record_id),
    )


async def _titles(ctx, table: str) -> list:
    listed = await pod_get_records(ctx, PodGetRecordsRequest(table_name=table))
    assert listed["success"] is True, listed
    return [r.get("title") for r in listed["records"]]


@pytest.mark.asyncio
async def test_the_assistants_record_delete_pauses_for_approval(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    """H14 / PS-ACCESS-021: a record delete is destructive. The assistant acts
    with its person's permissions, and its person may delete the row -- but the
    assistant asks first, and the row stays until someone says yes."""
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    table = f"notes_{uuid4().hex[:8]}"
    await _create_table(authenticated_client, pod_id, table)
    ctx = _run_ctx(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        workload_id=pod_id,
        agent_name="pod_default",
    )
    created = await pod_write_record(
        ctx,
        PodWriteRecordRequest(action="create", table_name=table, data={"title": "x"}),
    )
    assert created["success"] is True, created

    paused = await _delete_record(ctx, table, str(created["record"]["id"]))

    assert paused["success"] is False, paused
    assert paused["needs_approval"] is True
    assert paused["code"] == "DESTRUCTIVE_ACTION_REQUIRES_APPROVAL"
    assert paused["approval"]["permission_ids"] == ["datastore.record.delete"]
    assert await _titles(ctx, table) == ["x"]


@pytest.mark.asyncio
async def test_a_named_agent_granted_record_write_asks_to_delete(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    """H14: ``record.write`` used to carry deletes. A named agent holding it
    writes, and is asked about -- and only about -- the delete."""
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    table = f"orders_{uuid4().hex[:8]}"
    await _create_table(authenticated_client, pod_id, table)
    agent_name = f"writer_{uuid4().hex[:8]}"
    agent = await _create_agent(authenticated_client, pod_id, agent_name)
    await _grant(authenticated_client, pod_id, agent_name, table)
    ctx = _run_ctx(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        workload_id=agent["id"],
        agent_name=agent_name,
    )
    created = await pod_write_record(
        ctx,
        PodWriteRecordRequest(action="create", table_name=table, data={"title": "y"}),
    )
    assert created["success"] is True, created

    paused = await _delete_record(ctx, table, str(created["record"]["id"]))

    assert paused["needs_approval"] is True, paused
    assert paused["approval"]["permission_ids"] == ["datastore.record.delete"]
    assert await _titles(ctx, table) == ["y"]
