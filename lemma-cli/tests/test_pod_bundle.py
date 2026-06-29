from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from lemma_sdk.errors import LemmaAPIError
from lemma_cli.cli_core.sdk import _FlatPodProxy
from lemma_cli.cli_app.pod_bundle import (
    _export_pod_files,
    _build_app_bundle,
    _import_pod_files,
    _resource_dirs,
    diff_table_columns,
    export_pod_bundle,
    fetch_files_index,
    import_pod_bundle,
    load_resource_payload,
)
import lemma_cli.cli_app.pod_bundle as pod_bundle_module


class FakeClient(SimpleNamespace):
    def pod(self, pod_id):
        return _FlatPodProxy(self, pod_id)


def _plain(payload):
    """Convert typed SDK request models (e.g. CreateAgentRequest) to plain dicts."""
    return payload.to_dict() if hasattr(payload, "to_dict") else payload


def test_load_resource_payload_resolves_file_refs(tmp_path):
    resource_dir = tmp_path / "functions" / "hello-world"
    resource_dir.mkdir(parents=True)
    (resource_dir / "code.py").write_text("print('hello')\n", encoding="utf-8")
    (resource_dir / "config.json").write_text(json.dumps({"mode": "fast"}), encoding="utf-8")
    (resource_dir / "hello-world.json").write_text(
        json.dumps(
            {
                "name": "hello-world",
                "code": {"$file": "code.py"},
                "config": {"$json_file": "config.json"},
            }
        ),
        encoding="utf-8",
    )

    payload = load_resource_payload(resource_dir, "hello-world")

    assert payload == {
        "name": "hello-world",
        "code": "print('hello')\n",
        "config": {"mode": "fast"},
    }


def test_load_resource_payload_normalizes_legacy_agent_tool_sets(tmp_path):
    resource_dir = tmp_path / "agents" / "triage"
    resource_dir.mkdir(parents=True)
    (resource_dir / "triage.json").write_text(
        json.dumps({"name": "triage", "tool_sets": ["WORKSPACE_CLI"]}),
        encoding="utf-8",
    )

    payload = load_resource_payload(resource_dir, "triage")

    assert payload == {"name": "triage", "toolsets": ["WORKSPACE_CLI"]}


def test_diff_table_columns_detects_add_remove_and_incompatible():
    existing = {
        "primary_key_column": "id",
        "columns": [
            {"name": "id", "type": "TEXT", "required": True},
            {"name": "title", "type": "TEXT", "required": True},
            {"name": "legacy", "type": "TEXT", "required": False},
        ],
    }
    desired = {
        "primary_key_column": "id",
        "columns": [
            {"name": "id", "type": "TEXT", "required": True},
            {"name": "title", "type": "TEXT", "required": False},
            {"name": "status", "type": "TEXT", "required": True},
        ],
    }

    diff = diff_table_columns(existing, desired)

    assert diff.to_add == [{"name": "status", "type": "TEXT", "required": True}]
    assert diff.to_remove == ["legacy"]
    assert diff.incompatible == ["title"]


def test_diff_table_columns_ignores_server_only_column_metadata():
    existing = {
        "primary_key_column": "id",
        "columns": [
            {
                "name": "issue_id",
                "type": "TEXT",
                "required": True,
                "unique": False,
                "auto": False,
                "computed": False,
                "default": None,
                "description": None,
                "expression": None,
                "foreign_key": None,
                "max_length": None,
                "options": None,
                "type_params": None,
            },
            {
                "name": "body",
                "type": "TEXT",
                "required": True,
                "unique": False,
                "auto": False,
                "computed": False,
                "default": None,
                "description": None,
                "expression": None,
                "foreign_key": None,
                "max_length": None,
                "options": None,
                "type_params": None,
            },
        ],
    }
    desired = {
        "primary_key_column": "id",
        "columns": [
            {"name": "issue_id", "type": "TEXT", "required": True},
            {"name": "body", "type": "TEXT", "required": True},
        ],
    }

    diff = diff_table_columns(existing, desired)

    assert diff.to_add == []
    assert diff.to_remove == []
    assert diff.incompatible == []


def test_diff_table_columns_ignores_system_columns_from_export_and_live_schema():
    existing = {
        "primary_key_column": "id",
        "columns": [
            {"name": "id", "type": "UUID", "required": True},
            {"name": "title", "type": "TEXT", "required": True},
            {"name": "created_at", "type": "DATETIME", "system": True},
            {"name": "updated_at", "type": "DATETIME", "system": True},
            {"name": "user_id", "type": "UUID", "system": True},
        ],
    }
    desired = {
        "primary_key_column": "id",
        "columns": [
            {"name": "id", "type": "UUID", "required": True},
            {"name": "title", "type": "TEXT", "required": True},
            {"name": "created_at", "type": "DATETIME", "system": True},
            {"name": "updated_at", "type": "DATETIME", "system": True},
        ],
    }

    diff = diff_table_columns(existing, desired)

    assert diff.to_add == []
    assert diff.to_remove == []
    assert diff.incompatible == []


def test_export_pod_files_only_writes_pod_visible_folders(tmp_path: Path):
    items = {
        "folder_root": {
            "id": "folder_root",
            "name": "product_datasheets",
            "kind": "FOLDER",
            "visibility": "POD",
            "path": "/product_datasheets",
            "description": "Shared product PDFs",
        },
        "folder_child": {
            "id": "folder_child",
            "name": "indoor",
            "kind": "FOLDER",
            "visibility": "POD",
            "path": "/product_datasheets/indoor",
            "description": "Indoor range",
        },
        "private_folder": {
            "id": "private_folder",
            "name": "private_notes",
            "kind": "FOLDER",
            "visibility": "PRIVATE",
            "path": "/private_notes",
            "description": "Should not export",
        },
        "file_item": {
            "id": "file_item",
            "name": "spec-sheet.pdf",
            "kind": "FILE",
            "visibility": "POD",
            "path": "/product_datasheets/spec-sheet.pdf",
        },
    }
    client = FakeClient(files=None)

    from lemma_cli.cli_app import pod_bundle

    original_fetch = pod_bundle.fetch_files_index
    pod_bundle.fetch_files_index = lambda _client, _pod_id: ({None: []}, items)
    try:
        counts = _export_pod_files(client, "pod_123", tmp_path)
    finally:
        pod_bundle.fetch_files_index = original_fetch

    assert counts == {"folders": 2, "files": 0}
    assert (tmp_path / "files" / "product_datasheets" / ".folder.json").exists()
    assert (tmp_path / "files" / "product_datasheets" / "indoor" / ".folder.json").exists()
    assert not (tmp_path / "files" / "private_notes").exists()
    assert not (tmp_path / "files" / "product_datasheets" / "spec-sheet.pdf").exists()


def test_export_pod_bundle_skips_excluded_apps(tmp_path: Path):
    client = FakeClient(
        pods=SimpleNamespace(get=lambda pod_id: {"id": pod_id, "name": "demo-pod"}),
        tables=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        functions=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        agents=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        schedules=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        workflows=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        surfaces=SimpleNamespace(list=lambda pod_id, limit=100: {"items": []}),
        apps=SimpleNamespace(
            list=lambda pod_id, limit=1000: (_ for _ in ()).throw(AssertionError("apps.list should not be called"))
        ),
        files=SimpleNamespace(
            tree=lambda pod_id, root_path="/", files_per_directory=20: {
                "tree": {"path": "/", "name": "/", "kind": "FOLDER", "children": []}
            }
        ),
    )

    result = export_pod_bundle(
        client,
        pod_id="pod_123",
        output_dir=tmp_path,
        exclude={"apps"},
    )

    assert result["ok"] is True
    assert result["excluded"] == ["apps"]
    assert result["counts"]["apps"] == 0
    assert (tmp_path / "demo-pod" / "pod.json").exists()
    assert list((tmp_path / "demo-pod" / "apps").iterdir()) == []


def test_download_app_assets_prefers_unpacked_source_over_dist(tmp_path: Path):
    resource_dir = tmp_path / "apps" / "support_app"
    resource_dir.mkdir(parents=True)

    source_zip = BytesIO()
    with ZipFile(source_zip, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("package.json", '{"name":"app"}\n')
        archive.writestr("src/main.ts", "console.log('app');\n")

    client = FakeClient(
        apps=SimpleNamespace(
            download_source_archive=lambda pod_id, app_name: source_zip.getvalue(),
            download_dist_archive=lambda pod_id, app_name: b"pretend-dist",
        )
    )

    from lemma_cli.cli_app.pod_bundle import _download_app_assets

    _download_app_assets(client, "pod_123", "support_app", resource_dir)

    assert (resource_dir / "source" / "package.json").exists()
    assert (resource_dir / "source" / "src" / "main.ts").exists()
    assert not (resource_dir / "dist.zip").exists()


def test_export_pod_bundle_rejects_unknown_exclude_value(tmp_path: Path):
    client = FakeClient(pods=SimpleNamespace(get=lambda pod_id: {"id": pod_id, "name": "demo-pod"}))

    try:
        export_pod_bundle(
            client,
            pod_id="pod_123",
            output_dir=tmp_path,
            exclude={"bogus"},
        )
    except ValueError as exc:
        assert "Unknown export exclude value" in str(exc)
        assert "bogus" in str(exc)
    else:
        raise AssertionError("export_pod_bundle should reject unknown exclude values")


def test_export_pod_bundle_can_export_single_agent(tmp_path: Path):
    client = FakeClient(
        pods=SimpleNamespace(get=lambda pod_id: {"id": pod_id, "name": "demo-pod"}),
        agents=SimpleNamespace(
            list=lambda pod_id, limit=1000: {
                "items": [{"name": "triage"}, {"name": "research"}]
            },
            get=lambda pod_id, agent_name: {
                "id": f"agent_{agent_name}",
                "pod_id": pod_id,
                "name": agent_name,
                "instruction": f"Use {agent_name}.",
            },
            get_permissions=lambda pod_id, agent_name: {
                "agent_name": agent_name,
                "grants": [
                    {
                        "resource_type": "connector",
                        "resource_name": "slack",
                        "permission_ids": ["connector.use"],
                    }
                ],
            },
        ),
    )

    result = export_pod_bundle(
        client,
        pod_id="pod_123",
        output_dir=tmp_path,
        include={"agents"},
        names={"triage"},
    )

    assert result["ok"] is True
    assert result["included"] == ["agents"]
    assert result["names"] == ["triage"]
    assert result["counts"]["agents"] == 1
    agent_json = tmp_path / "demo-pod" / "agents" / "triage" / "triage.json"
    assert agent_json.exists()
    agent_payload = json.loads(agent_json.read_text(encoding="utf-8"))
    assert agent_payload["permissions"] == {
        "grants": [
            {
                "resource_type": "connector",
                "resource_name": "slack",
                "permission_ids": ["connector.use"],
            }
        ]
    }
    assert not (tmp_path / "demo-pod" / "agents" / "research").exists()


def test_export_pod_bundle_embeds_function_permissions(tmp_path: Path):
    client = FakeClient(
        pods=SimpleNamespace(get=lambda pod_id: {"id": pod_id, "name": "demo-pod"}),
        functions=SimpleNamespace(
            list=lambda pod_id, limit=1000: {"items": [{"name": "sync"}]},
            get=lambda pod_id, function_name: {
                "id": "function_sync",
                "pod_id": pod_id,
                "name": function_name,
                "description": "Sync.",
                "type": "API",
                "code": "#function_name: run\n",
            },
            get_permissions=lambda pod_id, function_name: {
                "function_name": function_name,
                "grants": [
                    {
                        "resource_type": "datastore_table",
                        "resource_name": "customers_table",
                        "permission_ids": ["datastore.table.read"],
                    }
                ],
            },
        ),
    )

    result = export_pod_bundle(
        client,
        pod_id="pod_123",
        output_dir=tmp_path,
        include={"functions"},
    )

    assert result["ok"] is True
    function_json = tmp_path / "demo-pod" / "functions" / "sync" / "sync.json"
    payload = json.loads(function_json.read_text(encoding="utf-8"))
    assert payload["code"] == {"$file": "code.py"}
    assert payload["permissions"] == {
        "grants": [
            {
                "resource_type": "datastore_table",
                "resource_name": "customers_table",
                "permission_ids": ["datastore.table.read"],
            }
        ]
    }


def test_import_pod_bundle_accepts_resource_collection_dir(tmp_path: Path):
    agents_root = tmp_path / "agents"
    agent_dir = agents_root / "triage"
    agent_dir.mkdir(parents=True)
    (agent_dir / "instruction.md").write_text("Handle triage.", encoding="utf-8")
    (agent_dir / "triage.json").write_text(
        json.dumps(
            {
                "name": "triage",
                "instruction": {"$file": "instruction.md"},
                "permissions": {
                    "grants": [
                        {
                            "resource_type": "connector",
                            "resource_name": "slack",
                            "permission_ids": ["connector.use"],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    created_payloads: list[tuple[str, dict[str, object]]] = []
    permission_payloads: list[tuple[str, str, dict[str, object]]] = []

    client = FakeClient(
        tables=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        functions=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        agents=SimpleNamespace(
            list=lambda pod_id, limit=1000: {"items": []},
            create=lambda pod_id, payload: created_payloads.append((pod_id, _plain(payload))) or {"name": _plain(payload)["name"]},
            replace_permissions=lambda pod_id, agent_name, payload: permission_payloads.append((pod_id, agent_name, _plain(payload)))
            or {"agent_name": agent_name, **_plain(payload)},
        ),
        workflows=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        apps=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        files=SimpleNamespace(
            tree=lambda pod_id, root_path="/", files_per_directory=20: {
                "tree": {"path": "/", "name": "/", "kind": "FOLDER", "children": []}
            }
        ),
    )

    result = import_pod_bundle(client, pod_id="pod_123", source_dir=agents_root)

    assert result["ok"] is True
    assert result["source_dir"] == str(agents_root)
    assert created_payloads == [
        ("pod_123", {"name": "triage", "instruction": "Handle triage."})
    ]
    assert permission_payloads == [
        (
            "pod_123",
            "triage",
            {
                "grants": [
                    {
                        "resource_type": "connector",
                        "resource_name": "slack",
                        "permission_ids": ["connector.use"],
                    }
                ]
            },
        )
    ]


def test_import_pod_bundle_agent_update_strips_name_and_clears_absent_schemas(
    tmp_path: Path,
):
    agents_root = tmp_path / "agents"
    agent_dir = agents_root / "triage"
    agent_dir.mkdir(parents=True)
    (agent_dir / "triage.json").write_text(
        json.dumps(
            {
                "name": "triage",
                "description": "Updated",
                "toolsets": ["WORKSPACE_CLI"],
                "permissions": {
                    "grants": [
                        {
                            "resource_type": "datastore_table",
                            "resource_name": "customers_table",
                            "permission_ids": ["datastore.table.read"],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    updated_payloads: list[tuple[str, str, dict[str, object]]] = []
    permission_payloads: list[tuple[str, str, dict[str, object]]] = []

    client = FakeClient(
        tables=SimpleNamespace(
            list=lambda pod_id, limit=1000: {"items": [{"name": "customers_table"}]}
        ),
        functions=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        agents=SimpleNamespace(
            list=lambda pod_id, limit=1000: {"items": [{"name": "triage"}]},
            get=lambda pod_id, agent_name: {
                "name": agent_name,
            },
            # _FlatPodProxy routes `.update(name, request)` to `update_graph(pod_id, name, **request.to_dict())`
            update_graph=lambda pod_id, agent_name, **payload: updated_payloads.append((pod_id, agent_name, payload))
            or {"name": agent_name},
            replace_permissions=lambda pod_id, agent_name, payload: permission_payloads.append((pod_id, agent_name, _plain(payload)))
            or {"agent_name": agent_name, **_plain(payload)},
        ),
        workflows=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        apps=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        files=SimpleNamespace(
            tree=lambda pod_id, root_path="/", files_per_directory=20: {
                "tree": {"path": "/", "name": "/", "kind": "FOLDER", "children": []}
            }
        ),
    )

    result = import_pod_bundle(client, pod_id="pod_123", source_dir=agents_root)

    assert result["ok"] is True
    # The bundle declares no input/output schema, so the update sends explicit
    # nulls to clear any stale schema on the existing agent (declarative sync).
    assert updated_payloads == [
        (
            "pod_123",
            "triage",
            {
                "description": "Updated",
                "toolsets": ["WORKSPACE_CLI"],
                "output_schema": None,
                "input_schema": None,
            },
        )
    ]
    assert permission_payloads == [
        (
            "pod_123",
            "triage",
            {
                "grants": [
                    {
                        "resource_type": "datastore_table",
                        "resource_name": "customers_table",
                        "permission_ids": ["datastore.table.read"],
                    }
                ]
            },
        )
    ]


def test_import_pod_bundle_rejects_grants_without_resource_name(tmp_path: Path):
    agents_root = tmp_path / "agents"
    agent_dir = agents_root / "triage"
    agent_dir.mkdir(parents=True)
    (agent_dir / "triage.json").write_text(
        json.dumps(
            {
                "name": "triage",
                "instruction": "Handle triage.",
                "permissions": {
                    "grants": [
                        {
                            "resource_type": "AGENT",
                            "resource_id": "00000000-0000-0000-0000-000000000999",
                            "permission_ids": ["agent.invoke"],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    client = FakeClient(
        tables=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        functions=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        agents=SimpleNamespace(
            list=lambda pod_id, limit=1000: {"items": []},
            create=lambda pod_id, payload: {"name": _plain(payload)["name"]},
            replace_permissions=lambda pod_id, agent_name, payload: (_ for _ in ()).throw(
                AssertionError("replace_permissions should not be called for invalid grants")
            ),
        ),
        workflows=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        apps=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        files=SimpleNamespace(
            tree=lambda pod_id, root_path="/", files_per_directory=20: {
                "tree": {"path": "/", "name": "/", "kind": "FOLDER", "children": []}
            }
        ),
    )

    with pytest.raises(ValueError, match="resource_name"):
        import_pod_bundle(client, pod_id="pod_123", source_dir=agents_root)


def test_fetch_files_index_uses_tree_payload_without_fetching_each_path():
    class _FilesApi:
        def tree(
            self,
            pod_id: str,
            root_path: str = "/",
            files_per_directory: int = 20,
        ) -> dict[str, object]:
            assert pod_id == "pod_123"
            assert root_path == "/"
            return {
                "tree": {
                    "path": "/",
                    "name": "/",
                    "kind": "FOLDER",
                    "children": [
                        {
                            "id": "folder_root",
                            "name": "product_datasheets",
                            "kind": "FOLDER",
                            "visibility": "POD",
                            "path": "/product_datasheets",
                            "children": [
                                {
                                    "id": "file_pdf",
                                    "name": "spec-sheet.pdf",
                                    "kind": "FILE",
                                    "visibility": "POD",
                                    "path": "/product_datasheets/spec-sheet.pdf",
                                }
                            ],
                        }
                    ],
                }
            }

        def get(self, pod_id: str, path: str) -> dict[str, object]:
            raise AssertionError(f"fetch_files_index should not call files.get for {pod_id}:{path}")

    by_parent, all_items = fetch_files_index(FakeClient(files=_FilesApi()), "pod_123")

    assert [item["path"] for item in by_parent[None]] == ["/product_datasheets"]
    assert [item["path"] for item in by_parent["/product_datasheets"]] == [
        "/product_datasheets/spec-sheet.pdf"
    ]
    assert all_items["folder_root"]["visibility"] == "POD"
    assert all_items["file_pdf"]["name"] == "spec-sheet.pdf"


def test_import_pod_files_only_creates_missing_folders(tmp_path: Path):
    files_root = tmp_path / "files"
    (files_root / "product_datasheets" / "indoor").mkdir(parents=True)
    (files_root / "product_datasheets" / ".folder.json").write_text(
        json.dumps({"description": "Shared product PDFs", "visibility": "POD"}),
        encoding="utf-8",
    )
    (files_root / "product_datasheets" / "indoor" / ".folder.json").write_text(
        json.dumps({"description": "Indoor range", "visibility": "POD"}),
        encoding="utf-8",
    )
    (files_root / "product_datasheets" / "indoor" / "ignored.pdf").write_text(
        "not exported",
        encoding="utf-8",
    )

    created_calls: list[dict[str, object]] = []
    existing_items = [
        {
            "id": "folder_root_existing",
            "name": "product_datasheets",
            "kind": "FOLDER",
            "visibility": "POD",
            "path": "/product_datasheets",
            "description": "Shared product PDFs",
        }
    ]

    def create_folder(
        pod_id: str,
        *,
        path: str,
        description: str | None = None,
        visibility: str | None = None,
    ) -> dict[str, object]:
        created_calls.append(
            {
                "pod_id": pod_id,
                "path": path,
                "description": description,
                "visibility": visibility,
            }
        )
        return {
            "id": f"created_{len(created_calls)}",
            "name": Path(str(path)).name,
            "path": path,
            "kind": "FOLDER",
            "visibility": visibility or "POD",
        }

    client = FakeClient(files=SimpleNamespace(create_folder=create_folder))

    from lemma_cli.cli_app import pod_bundle

    original_list = pod_bundle._list_pod_visible_items
    pod_bundle._list_pod_visible_items = lambda _client, _pod_id: existing_items
    try:
        summary = _import_pod_files(client, "pod_123", tmp_path)
    finally:
        pod_bundle._list_pod_visible_items = original_list

    assert summary == ["created-folder:product_datasheets/indoor"]
    assert created_calls == [
        {
            "pod_id": "pod_123",
            "path": "/product_datasheets/indoor",
            "description": "Indoor range",
            "visibility": "POD",
        }
    ]


def test_import_pod_files_updates_existing_folder_metadata_on_conflict(tmp_path: Path):
    files_root = tmp_path / "files"
    (files_root / "DEMO_SKILLS").mkdir(parents=True)
    (files_root / "DEMO_SKILLS" / ".folder.json").write_text(
        json.dumps({"description": "Shared skills", "visibility": "POD"}),
        encoding="utf-8",
    )

    update_calls: list[dict[str, object]] = []

    def create_folder(pod_id: str, *, path: str, description: str | None = None, visibility: str | None = None):
        raise LemmaAPIError(status_code=409, message="Exists", code="DATASTORE_CONFLICT")

    def get_file(pod_id: str, path: str) -> dict[str, object]:
        assert pod_id == "pod_123"
        assert path == "/DEMO_SKILLS"
        return {
            "id": "folder_existing",
            "name": "DEMO_SKILLS",
            "kind": "FOLDER",
            "visibility": "PRIVATE",
            "path": "/DEMO_SKILLS",
            "description": "Old private folder",
        }

    def update_file(pod_id: str, path: str, body) -> dict[str, object]:
        payload = body.to_dict()
        update_calls.append(
            {
                "pod_id": pod_id,
                "path": path,
                "description": payload.get("description"),
                "visibility": payload.get("visibility"),
            }
        )
        return {
            "id": "folder_existing",
            "name": "DEMO_SKILLS",
            "kind": "FOLDER",
            "visibility": payload.get("visibility") or "PRIVATE",
            "path": path,
            "description": payload.get("description"),
        }

    client = FakeClient(
        files=SimpleNamespace(
            create_folder=create_folder,
            get=get_file,
            update=update_file,
        )
    )

    from lemma_cli.cli_app import pod_bundle

    original_list = pod_bundle._list_pod_visible_items
    pod_bundle._list_pod_visible_items = lambda _client, _pod_id: []
    try:
        summary = _import_pod_files(client, "pod_123", tmp_path)
    finally:
        pod_bundle._list_pod_visible_items = original_list

    assert summary == ["updated-folder:DEMO_SKILLS"]
    assert update_calls == [
        {
            "pod_id": "pod_123",
            "path": "/DEMO_SKILLS",
            "description": "Shared skills",
            "visibility": "POD",
        }
    ]


def test_import_pod_bundle_function_update_strips_config(tmp_path: Path):
    (tmp_path / "pod.json").write_text(
        json.dumps({"name": "demo", "format_version": 1}),
        encoding="utf-8",
    )
    function_dir = tmp_path / "functions" / "sync_demo_products"
    function_dir.mkdir(parents=True)
    (function_dir / "code.py").write_text(
        "#input_type_name: SyncInput\n"
        "#output_type_name: SyncOutput\n"
        "#function_name: sync_demo_products\n\n"
        "from pydantic import BaseModel\n\n"
        "class SyncInput(BaseModel):\n"
        "    pass\n\n"
        "class SyncOutput(BaseModel):\n"
        "    ok: bool = True\n",
        encoding="utf-8",
    )
    (function_dir / "sync_demo_products.json").write_text(
        json.dumps(
            {
                "name": "sync_demo_products",
                "description": "Sync test",
                "type": "JOB",
                "config": {"api_key": "should-not-be-sent"},
                "code": {"$file": "code.py"},
                "permissions": {
                    "grants": [
                        {
                            "resource_type": "datastore_table",
                            "resource_name": "products_table",
                            "permission_ids": [
                                "datastore.table.read",
                                "datastore.record.write",
                            ],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    update_payloads: list[tuple[str, str, dict[str, object]]] = []
    permission_payloads: list[tuple[str, str, dict[str, object]]] = []

    client = FakeClient(
        pods=SimpleNamespace(update=lambda pod_id, request: {"id": pod_id, **_plain(request)}),
        tables=SimpleNamespace(
            list=lambda pod_id, limit=1000: {"items": [{"name": "products_table"}]}
        ),
        functions=SimpleNamespace(
            list=lambda pod_id, limit=1000: {"items": [{"name": "sync_demo_products"}]},
            # _FlatPodProxy routes `.update(name, request)` to `update_graph(pod_id, name, **request.to_dict())`
            update_graph=lambda pod_id, function_name, **payload: update_payloads.append((pod_id, function_name, payload)) or {"name": function_name},
            replace_permissions=lambda pod_id, function_name, payload: permission_payloads.append((pod_id, function_name, _plain(payload)))
            or {"function_name": function_name, **_plain(payload)},
        ),
        agents=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        workflows=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        apps=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        files=SimpleNamespace(
            tree=lambda pod_id, root_path="/", files_per_directory=20: {
                "tree": {"path": "/", "name": "/", "kind": "FOLDER", "children": []}
            }
        ),
    )

    result = import_pod_bundle(client, pod_id="pod_123", source_dir=tmp_path)

    assert result["ok"] is True
    assert len(update_payloads) == 1
    assert update_payloads[0][2] == {
        "description": "Sync test",
        "type": "JOB",
        "code": (
            "#input_type_name: SyncInput\n"
            "#output_type_name: SyncOutput\n"
            "#function_name: sync_demo_products\n\n"
            "from pydantic import BaseModel\n\n"
            "class SyncInput(BaseModel):\n"
            "    pass\n\n"
            "class SyncOutput(BaseModel):\n"
            "    ok: bool = True\n"
        ),
    }
    assert permission_payloads == [
        (
            "pod_123",
            "sync_demo_products",
            {
                "grants": [
                    {
                        "resource_type": "datastore_table",
                        "resource_name": "products_table",
                        "permission_ids": [
                            "datastore.table.read",
                            "datastore.record.write",
                        ],
                    }
                ]
            },
        )
    ]


def test_import_pod_bundle_table_create_strips_system_columns(tmp_path: Path):
    (tmp_path / "pod.json").write_text(
        json.dumps({"name": "demo", "format_version": 1}),
        encoding="utf-8",
    )
    table_dir = tmp_path / "tables" / "expenses"
    table_dir.mkdir(parents=True)
    (table_dir / "expenses.json").write_text(
        json.dumps(
            {
                "name": "expenses",
                "enable_rls": True,
                "primary_key_column": "id",
                "columns": [
                    {"name": "id", "type": "UUID", "required": True, "auto": True},
                    {"name": "title", "type": "TEXT", "required": True},
                    {"name": "created_at", "type": "DATETIME", "system": True},
                    {"name": "updated_at", "type": "DATETIME", "system": True},
                    {"name": "user_id", "type": "UUID", "system": True},
                ],
            }
        ),
        encoding="utf-8",
    )

    create_payloads: list[dict[str, object]] = []

    client = FakeClient(
        pods=SimpleNamespace(update=lambda pod_id, request: {"id": pod_id, **_plain(request)}),
        tables=SimpleNamespace(
            list=lambda pod_id, limit=1000: {"items": []},
            create=lambda pod_id, payload: create_payloads.append(_plain(payload)) or {"name": _plain(payload)["name"]},
        ),
        functions=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        agents=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        workflows=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        apps=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        files=SimpleNamespace(
            tree=lambda pod_id, root_path="/", files_per_directory=20: {
                "tree": {"path": "/", "name": "/", "kind": "FOLDER", "children": []}
            }
        ),
    )

    result = import_pod_bundle(client, pod_id="pod_123", source_dir=tmp_path)

    assert result["ok"] is True
    assert len(create_payloads) == 1
    assert create_payloads[0]["columns"] == [
        {"name": "id", "type": "UUID", "required": True, "auto": True},
        {"name": "title", "type": "TEXT", "required": True},
    ]


def _reserved_column_bundle(tmp_path: Path, *, enable_rls: bool = True) -> None:
    (tmp_path / "pod.json").write_text(
        json.dumps({"name": "demo", "format_version": 1}), encoding="utf-8"
    )
    table_dir = tmp_path / "tables" / "presence"
    table_dir.mkdir(parents=True)
    (table_dir / "presence.json").write_text(
        json.dumps(
            {
                "name": "presence",
                "enable_rls": enable_rls,
                "primary_key_column": "id",
                "columns": [
                    {"name": "user_id", "type": "TEXT"},
                    {"name": "status", "type": "TEXT"},
                ],
            }
        ),
        encoding="utf-8",
    )


def _no_resources_client() -> "FakeClient":
    return FakeClient(
        tables=SimpleNamespace(
            list=lambda pod_id, limit=1000: {"items": []},
            create=lambda pod_id, payload: {"name": _plain(payload)["name"]},
        ),
        functions=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        agents=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        workflows=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        apps=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        files=SimpleNamespace(
            tree=lambda pod_id, root_path="/", files_per_directory=20: {
                "tree": {"path": "/", "name": "/", "kind": "FOLDER", "children": []}
            }
        ),
    )


def test_import_pod_bundle_dry_run_flags_declared_system_column(tmp_path: Path):
    _reserved_column_bundle(tmp_path)

    result = import_pod_bundle(
        _no_resources_client(), pod_id="pod_123", source_dir=tmp_path, dry_run=True
    )

    assert result["ok"] is False
    assert any(
        "system-managed" in err["message"] and "user_id" in err["message"]
        for err in result["errors"]
    )


def test_import_pod_bundle_rejects_declared_system_column(tmp_path: Path):
    _reserved_column_bundle(tmp_path)

    with pytest.raises(ValueError, match="user_id"):
        import_pod_bundle(_no_resources_client(), pod_id="pod_123", source_dir=tmp_path)


def test_import_pod_bundle_allows_user_id_without_rls(tmp_path: Path):
    # user_id is only system-managed when RLS is enabled; without it the author may
    # define their own user_id column.
    _reserved_column_bundle(tmp_path, enable_rls=False)

    result = import_pod_bundle(
        _no_resources_client(), pod_id="pod_123", source_dir=tmp_path, dry_run=True
    )

    assert result["ok"] is True


def test_import_pod_bundle_dry_run_reports_validation_errors(tmp_path: Path):
    (tmp_path / "pod.json").write_text(json.dumps({"name": "demo", "format_version": 1}), encoding="utf-8")
    function_dir = tmp_path / "functions" / "broken_function"
    function_dir.mkdir(parents=True)
    (function_dir / "code.py").write_text("def not_valid(:\n", encoding="utf-8")
    (function_dir / "broken_function.json").write_text(
        json.dumps(
            {
                "name": "broken_function",
                "description": "Bad syntax test",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "code": {"$file": "code.py"},
            }
        ),
        encoding="utf-8",
    )

    client = FakeClient(
        tables=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        functions=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        agents=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        workflows=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        apps=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        files=SimpleNamespace(
            tree=lambda pod_id, root_path="/", files_per_directory=20: {
                "tree": {"path": "/", "name": "/", "kind": "FOLDER", "children": []}
            }
        ),
    )

    result = import_pod_bundle(client, pod_id="pod_123", source_dir=tmp_path, dry_run=True)

    assert result["ok"] is False
    assert result["dry_run"] is True
    assert result["summary"]["functions"] == ["created:broken_function"]
    assert result["errors"]
    assert "Python syntax error" in result["errors"][0]["message"]


def test_build_app_bundle_uses_npm_ci_when_lockfile_exists(tmp_path: Path):
    resource_dir = tmp_path / "apps" / "support_app"
    source_dir = resource_dir / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "package.json").write_text('{"name":"app"}\n', encoding="utf-8")
    (source_dir / "package-lock.json").write_text('{"lockfileVersion": 3}\n', encoding="utf-8")

    from lemma_cli.cli_app import pod_bundle

    calls: list[tuple[list[str], Path, bool, dict[str, str] | None]] = []
    original_run = pod_bundle._run_command

    def fake_run(command: list[str], *, cwd: Path, stream_output: bool, env=None):
        calls.append((command, cwd, stream_output, env))
        if command == ["npm", "run", "build"]:
            dist_dir = source_dir / "dist"
            dist_dir.mkdir()
            (dist_dir / "index.html").write_text("<html>fresh</html>", encoding="utf-8")
        return SimpleNamespace(stdout="", stderr="")

    pod_bundle._run_command = fake_run
    try:
        dist_path = _build_app_bundle(
            resource_dir,
            stream_output=False,
        )
    finally:
        pod_bundle._run_command = original_run

    assert dist_path == resource_dir / "dist.zip"
    with ZipFile(dist_path) as archive:
        assert archive.read("index.html").decode("utf-8") == "<html>fresh</html>"
    assert [call[:3] for call in calls] == [
        (["npm", "ci"], source_dir, False),
        (["npm", "run", "build"], source_dir, False),
    ]
    assert calls[0][3] is None
    assert calls[1][3] is None


def test_deploy_app_bundle_builds_with_project_env(
    tmp_path: Path,
    monkeypatch,
):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "package.json").write_text('{"name":"app"}\n', encoding="utf-8")
    (source_dir / ".env.local").write_text(
        "\n".join(
            [
                "VITE_LEMMA_API_URL=https://api.example.test",
                "VITE_LEMMA_AUTH_URL=https://auth.example.test",
                "VITE_LEMMA_POD_ID=pod_123",
                "VITE_LEMMA_APP_BASE_PATH=/",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    from lemma_cli.cli_app import app_bundle

    calls: list[dict[str, object]] = []

    def fake_run(command, *, cwd, env, check):
        calls.append({"command": command, "cwd": cwd, "env": env, "check": check})
        dist = source_dir / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html>fresh</html>", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(app_bundle.subprocess, "run", fake_run)

    uploads: list[dict[str, object]] = []

    class FakeApps:
        def get(self, pod_id, app_name):
            return {"name": app_name, "public_slug": "ops-app"}

        def upload_bundle(self, pod_id, app_name, *, source_archive, dist_archive):
            uploads.append(
                {
                    "pod_id": pod_id,
                    "app_name": app_name,
                    "source_archive": source_archive,
                    "dist_archive": dist_archive,
                }
            )
            return {"ok": True}

    client = FakeClient(apps=FakeApps())

    result = app_bundle.deploy_app_bundle(
        client,
        pod_id="pod_123",
        app_name="support_app",
        source_dir=source_dir,
    )

    assert result == {"ok": True}
    assert calls[0]["command"] == [
        "npm",
        "run",
        "build",
    ]
    env = calls[0]["env"]
    assert env["VITE_LEMMA_API_URL"] == "https://api.example.test"
    assert env["VITE_LEMMA_AUTH_URL"] == "https://auth.example.test"
    assert env["VITE_LEMMA_POD_ID"] == "pod_123"
    assert env["VITE_LEMMA_APP_BASE_PATH"] == "/"
    assert len(uploads) == 1


def test_build_app_bundle_falls_back_to_npm_install_without_lockfile(tmp_path: Path):
    resource_dir = tmp_path / "apps" / "support_app"
    source_dir = resource_dir / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "package.json").write_text('{"name":"app"}\n', encoding="utf-8")

    from lemma_cli.cli_app import pod_bundle

    calls: list[tuple[list[str], Path, bool, dict[str, str] | None]] = []
    original_run = pod_bundle._run_command

    def fake_run(command: list[str], *, cwd: Path, stream_output: bool, env=None):
        calls.append((command, cwd, stream_output, env))
        if command == ["npm", "run", "build"]:
            dist_dir = source_dir / "dist"
            dist_dir.mkdir()
            (dist_dir / "index.html").write_text("<html>fresh</html>", encoding="utf-8")
        return SimpleNamespace(stdout="", stderr="")

    pod_bundle._run_command = fake_run
    try:
        dist_path = _build_app_bundle(resource_dir, stream_output=False)
    finally:
        pod_bundle._run_command = original_run

    assert dist_path == resource_dir / "dist.zip"
    assert calls == [
        (["npm", "install"], source_dir, False, None),
        (["npm", "run", "build"], source_dir, False, None),
    ]


def test_import_pod_bundle_dry_run_validates_app_build(tmp_path: Path):
    (tmp_path / "pod.json").write_text(json.dumps({"name": "demo", "format_version": 1}), encoding="utf-8")
    app_dir = tmp_path / "apps" / "support_app"
    source_dir = app_dir / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "package.json").write_text('{"name":"app"}\n', encoding="utf-8")
    (app_dir / "support_app.json").write_text(
        json.dumps({"name": "support_app", "description": "app"}),
        encoding="utf-8",
    )

    client = FakeClient(
        tables=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        functions=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        agents=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        workflows=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        apps=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        files=SimpleNamespace(
            tree=lambda pod_id, root_path="/", files_per_directory=20: {
                "tree": {"path": "/", "name": "/", "kind": "FOLDER", "children": []}
            }
        ),
    )

    from lemma_cli.cli_app import pod_bundle

    original_build = pod_bundle._build_app_bundle
    pod_bundle._build_app_bundle = lambda resource_dir, stream_output: "<html>built</html>"
    try:
        result = import_pod_bundle(client, pod_id="pod_123", source_dir=tmp_path, dry_run=True)
    finally:
        pod_bundle._build_app_bundle = original_build

    assert result["ok"] is True
    assert result["summary"]["apps"] == ["created:support_app"]


def test_import_pod_bundle_validates_and_uploads_app_source(tmp_path: Path):
    (tmp_path / "pod.json").write_text(json.dumps({"name": "demo", "format_version": 1}), encoding="utf-8")
    app_dir = tmp_path / "apps" / "support_app"
    source_dir = app_dir / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "package.json").write_text('{"name":"app"}\n', encoding="utf-8")
    (app_dir / "support_app.json").write_text(
        json.dumps({"name": "support_app", "description": "app"}),
        encoding="utf-8",
    )

    deploy_calls: list[dict[str, object]] = []

    client = FakeClient(
        pods=SimpleNamespace(update=lambda pod_id, request: {"id": pod_id, **_plain(request)}),
        tables=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        functions=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        agents=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        workflows=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        apps=SimpleNamespace(
            list=lambda pod_id, limit=1000: {"items": []},
            create=lambda pod_id, payload: {"name": _plain(payload)["name"]},
        ),
        files=SimpleNamespace(
            tree=lambda pod_id, root_path="/", files_per_directory=20: {
                "tree": {"path": "/", "name": "/", "kind": "FOLDER", "children": []}
            }
        ),
    )

    from lemma_cli.cli_app import pod_bundle

    original_build = pod_bundle._build_app_bundle
    original_deploy = pod_bundle.deploy_app_bundle

    def fake_deploy(client, *, pod_id: str, app_name: str, source_dir: Path, dist_dir=None, ensure_exists=False):
        deploy_calls.append(
            {
                "pod_id": pod_id,
                "app_name": app_name,
                "source_dir": source_dir,
                "dist_dir": dist_dir,
                "ensure_exists": ensure_exists,
            }
        )
        return {"ok": True}

    pod_bundle._build_app_bundle = (
        lambda resource_dir, stream_output: resource_dir / "dist.zip"
    )
    pod_bundle.deploy_app_bundle = fake_deploy
    try:
        result = import_pod_bundle(
            client,
            pod_id="pod_123",
            source_dir=tmp_path,
        )
    finally:
        pod_bundle._build_app_bundle = original_build
        pod_bundle.deploy_app_bundle = original_deploy

    assert result["ok"] is True
    assert deploy_calls == [
        {
            "pod_id": "pod_123",
            "app_name": "support_app",
            "source_dir": source_dir,
            "dist_dir": None,
            "ensure_exists": False,
        },
    ]


def test_import_pod_bundle_retries_app_create_with_unique_public_slug_on_conflict(tmp_path: Path):
    (tmp_path / "pod.json").write_text(json.dumps({"name": "demo", "format_version": 1}), encoding="utf-8")
    app_dir = tmp_path / "apps" / "support_app"
    source_dir = app_dir / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "package.json").write_text('{"name":"app"}\n', encoding="utf-8")
    (app_dir / "support_app.json").write_text(
        json.dumps(
            {
                "name": "support_app",
                "public_slug": "support-app",
                "description": "app",
            }
        ),
        encoding="utf-8",
    )

    create_payloads: list[dict[str, object]] = []

    def create_app(pod_id: str, payload) -> dict[str, object]:
        plain = _plain(payload)
        create_payloads.append(plain)
        if len(create_payloads) == 1:
            raise LemmaAPIError(status_code=409, message="App with public slug exists", code="APP_CONFLICT")
        return {"name": plain["name"]}

    client = FakeClient(
        pods=SimpleNamespace(update=lambda pod_id, request: {"id": pod_id, **_plain(request)}),
        tables=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        functions=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        agents=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        workflows=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        apps=SimpleNamespace(
            list=lambda pod_id, limit=1000: {"items": []},
            create=create_app,
            upload_bundle=lambda pod_id, app_name, source_archive=None, dist_archive=None: {},
        ),
        files=SimpleNamespace(
            tree=lambda pod_id, root_path="/", files_per_directory=20: {
                "tree": {"path": "/", "name": "/", "kind": "FOLDER", "children": []}
            }
        ),
    )

    from lemma_cli.cli_app import pod_bundle

    original_build = pod_bundle._build_app_bundle
    original_deploy = pod_bundle.deploy_app_bundle
    pod_bundle._build_app_bundle = (
        lambda resource_dir, stream_output: resource_dir / "dist.zip"
    )
    pod_bundle.deploy_app_bundle = (
        lambda client, *, pod_id, app_name, source_dir, dist_dir=None, ensure_exists=False: {}
    )
    try:
        result = import_pod_bundle(client, pod_id="pod_123", source_dir=tmp_path)
    finally:
        pod_bundle._build_app_bundle = original_build
        pod_bundle.deploy_app_bundle = original_deploy

    assert result["ok"] is True
    assert create_payloads == [
        {
            "name": "support_app",
            "public_slug": "support-app",
            "description": "app",
        },
        {
            "name": "support_app",
            "public_slug": "support-app-pod123",
            "description": "app",
        },
    ]


def test_export_pod_bundle_writes_normalized_surfaces(tmp_path: Path):
    client = FakeClient(
        pods=SimpleNamespace(get=lambda pod_id: {"id": pod_id, "name": "demo-pod"}),
        surfaces=SimpleNamespace(
            list=lambda pod_id, limit=100: {
                "items": [
                    {
                        "id": "surface_1",
                        "pod_id": pod_id,
                        "platform": "SLACK",
                        "surface_type": "SLACK",
                        "agent_id": "agent_1",
                        "agent_name": "triage-agent",
                        "credential_mode": "CUSTOM",
                        "account_id": "00000000-0000-0000-0000-00000000aaaa",
                        "status": "ACTIVE",
                        "webhook_url": "https://example.com/hook",
                        "surface_identity_id": "B123",
                        "config": {
                            "type": "SLACK",
                            "id": "cfg_1",
                            "dm": {"enabled": True},
                            "setup": {"step": "done"},
                            "channels": [
                                {
                                    "enabled": True,
                                    "channel_id": "C123",
                                    "channel_name": "support",
                                    "agent_name": "triage-agent",
                                    "external_workspace_id": "T123",
                                },
                                {"enabled": False, "channel_id": "C999"},
                            ],
                            "identity": {
                                "allowed_domains": ["example.com"],
                                "allowed_email_addresses": [],
                                "internal_field": "x",
                            },
                        },
                    }
                ]
            }
        ),
    )

    result = export_pod_bundle(
        client,
        pod_id="pod_123",
        output_dir=tmp_path,
        include={"surfaces"},
    )

    assert result["ok"] is True
    assert result["counts"]["surfaces"] == 1
    # The non-portable account id is extracted into a pod.json variable.
    assert result["variables"] == ["slack_account"]
    surface_json = tmp_path / "demo-pod" / "surfaces" / "slack" / "slack.json"
    assert surface_json.exists()
    payload = json.loads(surface_json.read_text(encoding="utf-8"))
    assert payload == {
        "name": "slack",
        "platform": "SLACK",
        "default_agent_name": "triage-agent",
        "credential_mode": "CUSTOM",
        "account_id": "${slack_account}",
        "is_enabled": True,
        "config": {
            "channels": [
                {
                    "channel_id": "C123",
                    "channel_name": "support",
                    "agent_name": "triage-agent",
                }
            ],
            "identity": {"allowed_domains": ["example.com"]},
        },
    }


def test_import_pod_bundle_upserts_surfaces_by_platform(tmp_path: Path):
    surfaces_root = tmp_path / "surfaces"
    slack_dir = surfaces_root / "slack"
    slack_dir.mkdir(parents=True)
    (slack_dir / "slack.json").write_text(
        json.dumps(
            {
                "name": "slack",
                "platform": "SLACK",
                "default_agent_name": "triage-agent",
                "credential_mode": "CUSTOM",
                "is_enabled": True,
                "config": {"identity": {"allowed_domains": ["example.com"]}},
            }
        ),
        encoding="utf-8",
    )
    telegram_dir = surfaces_root / "telegram"
    telegram_dir.mkdir(parents=True)
    (telegram_dir / "telegram.json").write_text(
        json.dumps(
            {
                "name": "telegram",
                "platform": "TELEGRAM",
                "default_agent_name": "inbox-agent",
                "is_enabled": False,
            }
        ),
        encoding="utf-8",
    )

    upserted: list[tuple[str, str, dict[str, object]]] = []

    client = FakeClient(
        tables=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        functions=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        agents=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        workflows=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        apps=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        surfaces=SimpleNamespace(
            list=lambda pod_id, limit=100: {
                "items": [{"id": "surface_1", "platform": "SLACK"}]
            },
            upsert=lambda pod_id, platform, payload: upserted.append(
                (pod_id, platform, _plain(payload))
            )
            or {"id": "surface_1", "platform": platform},
        ),
        files=SimpleNamespace(
            tree=lambda pod_id, root_path="/", files_per_directory=20: {
                "tree": {"path": "/", "name": "/", "kind": "FOLDER", "children": []}
            }
        ),
    )

    result = import_pod_bundle(client, pod_id="pod_123", source_dir=surfaces_root)

    assert result["ok"] is True
    assert sorted(result["summary"]["surfaces"]) == [
        "created:telegram",
        "updated:slack",
    ]
    assert upserted == [
        (
            "pod_123",
            "SLACK",
            {
                "default_agent_name": "triage-agent",
                "credential_mode": "CUSTOM",
                "is_enabled": True,
                "config": {"identity": {"allowed_domains": ["example.com"]}},
            },
        ),
        (
            "pod_123",
            "TELEGRAM",
            {"default_agent_name": "inbox-agent", "is_enabled": False},
        ),
    ]


def test_import_pod_bundle_rejects_unknown_surface_platform(tmp_path: Path):
    surfaces_root = tmp_path / "surfaces"
    bogus_dir = surfaces_root / "myspace"
    bogus_dir.mkdir(parents=True)
    (bogus_dir / "myspace.json").write_text(
        json.dumps({"name": "myspace", "platform": "MYSPACE"}),
        encoding="utf-8",
    )

    client = FakeClient(
        tables=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        functions=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        agents=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        workflows=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        apps=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        surfaces=SimpleNamespace(list=lambda pod_id, limit=100: {"items": []}),
        files=SimpleNamespace(
            tree=lambda pod_id, root_path="/", files_per_directory=20: {
                "tree": {"path": "/", "name": "/", "kind": "FOLDER", "children": []}
            }
        ),
    )

    result = import_pod_bundle(
        client, pod_id="pod_123", source_dir=surfaces_root, dry_run=True
    )

    assert result["ok"] is False
    assert any("Unknown surface platform" in error["message"] for error in result["errors"])


def test_import_pod_bundle_applies_workflow_graph(tmp_path: Path):
    workflows_root = tmp_path / "workflows"
    workflow_dir = workflows_root / "intake"
    workflow_dir.mkdir(parents=True)
    nodes = [
        {"id": "start_fn", "type": "FUNCTION", "config": {"function_name": "create_ticket"}},
        {"id": "end", "type": "END"},
    ]
    edges = [{"id": "e1", "source": "start_fn", "target": "end"}]
    (workflow_dir / "intake.json").write_text(
        json.dumps(
            {
                "name": "intake",
                "description": "Intake flow",
                "start": {"type": "MANUAL"},
                "nodes": nodes,
                "edges": edges,
            }
        ),
        encoding="utf-8",
    )

    created: list[dict[str, object]] = []
    graphs: list[tuple[str, str, dict[str, object]]] = []

    client = FakeClient(
        tables=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        functions=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        agents=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        workflows=SimpleNamespace(
            list=lambda pod_id, limit=1000: {"items": []},
            create=lambda pod_id, payload: created.append(_plain(payload)) or {"name": "intake"},
            update_graph=lambda pod_id, name, payload: graphs.append((pod_id, name, _plain(payload)))
            or {"name": name},
        ),
        apps=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        files=SimpleNamespace(
            tree=lambda pod_id, root_path="/", files_per_directory=20: {
                "tree": {"path": "/", "name": "/", "kind": "FOLDER", "children": []}
            }
        ),
    )

    result = import_pod_bundle(client, pod_id="pod_123", source_dir=workflows_root)

    assert result["ok"] is True
    assert result["summary"]["workflows"] == ["created:intake"]
    assert created and created[0]["name"] == "intake"
    assert graphs == [
        (
            "pod_123",
            "intake",
            {"nodes": nodes, "edges": edges, "start": {"type": "MANUAL"}},
        )
    ]


def test_order_table_dirs_respects_foreign_keys(tmp_path: Path):
    from lemma_cli.cli_app.pod_bundle import _order_table_dirs_by_dependency

    tables_root = tmp_path / "tables"
    # ticket_events references tickets; alphabetical order would create it first.
    events_dir = tables_root / "ticket_events"
    events_dir.mkdir(parents=True)
    (events_dir / "ticket_events.json").write_text(
        json.dumps(
            {
                "name": "ticket_events",
                "columns": [
                    {
                        "name": "ticket_id",
                        "type": "UUID",
                        "foreign_key": {"references": "tickets.id"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    tickets_dir = tables_root / "tickets"
    tickets_dir.mkdir(parents=True)
    (tickets_dir / "tickets.json").write_text(
        json.dumps({"name": "tickets", "columns": [{"name": "title", "type": "TEXT"}]}),
        encoding="utf-8",
    )

    ordered = _order_table_dirs_by_dependency([events_dir, tickets_dir])
    assert [path.name for path in ordered] == ["tickets", "ticket_events"]


def _empty_files_client() -> "FakeClient":
    return FakeClient(
        tables=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        functions=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        agents=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        workflows=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        schedules=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        surfaces=SimpleNamespace(list=lambda pod_id, limit=100: {"items": []}),
        apps=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        files=SimpleNamespace(
            tree=lambda pod_id, root_path="/", files_per_directory=20: {
                "tree": {"path": "/", "name": "/", "kind": "FOLDER", "children": []}
            }
        ),
    )


def test_import_pod_bundle_ignores_empty_resource_dir(tmp_path: Path):
    """An author deleted a starter resource's JSON but left the empty directory
    (e.g. `tables/items/` with no `items.json`). The leftover dir must be treated
    as "not a resource" and ignored, not break the import plan."""
    (tmp_path / "pod.json").write_text(
        json.dumps({"name": "demo", "format_version": 1}), encoding="utf-8"
    )
    tables_root = tmp_path / "tables"

    real_table = tables_root / "expenses"
    real_table.mkdir(parents=True)
    (real_table / "expenses.json").write_text(
        json.dumps(
            {
                "name": "expenses",
                "columns": [{"name": "title", "type": "TEXT", "required": True}],
            }
        ),
        encoding="utf-8",
    )

    # Empty leftover: directory exists but has no `items.json` (or no JSON at all).
    (tables_root / "items").mkdir(parents=True)

    result = import_pod_bundle(
        _empty_files_client(), pod_id="pod_123", source_dir=tmp_path, dry_run=True
    )

    assert result["ok"] is True
    assert result["errors"] == []
    # Only the real table is in the plan; the empty `items/` dir is ignored.
    assert result["summary"]["tables"] == ["created:expenses"]


def test_import_pod_bundle_accepts_lemma_app_json_manifest(tmp_path: Path):
    """Some app scaffolds write `lemma.app.json` instead of `<name>.json`.
    The importer should accept it as the app's manifest."""
    (tmp_path / "pod.json").write_text(
        json.dumps({"name": "demo", "format_version": 1}), encoding="utf-8"
    )
    app_dir = tmp_path / "apps" / "spark_board"
    app_dir.mkdir(parents=True)
    (app_dir / "lemma.app.json").write_text(
        json.dumps({"name": "spark_board", "description": "Spark Board"}),
        encoding="utf-8",
    )

    create_payloads: list[dict[str, object]] = []

    client = _empty_files_client()
    client.pods = SimpleNamespace(
        update=lambda pod_id, request: {"id": pod_id, **_plain(request)}
    )
    client.apps = SimpleNamespace(
        list=lambda pod_id, limit=1000: {"items": []},
        create=lambda pod_id, payload: create_payloads.append(_plain(payload))
        or {"name": _plain(payload)["name"]},
    )

    result = import_pod_bundle(client, pod_id="pod_123", source_dir=tmp_path)

    assert result["ok"] is True
    assert result["summary"]["apps"] == ["created:spark_board"]
    assert create_payloads == [{"name": "spark_board", "description": "Spark Board"}]


# --- $POD_MEMBER import resolution (export tokenizes; import must substitute) ---


def _write_tokenized_workflow(root: Path, name: str = "approval") -> None:
    wf_dir = root / "workflows" / name
    wf_dir.mkdir(parents=True)
    (wf_dir / f"{name}.json").write_text(
        json.dumps(
            {
                "name": name,
                "nodes": [
                    {
                        "id": "n1",
                        "type": "FORM",
                        "config": {"assignee_pod_member_id": "$POD_MEMBER"},
                    }
                ],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )


def _import_client(recorded, *, members, profile_id="user-1"):
    return FakeClient(
        tables=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        functions=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        agents=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        schedules=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        surfaces=SimpleNamespace(list=lambda pod_id, limit=100: {"items": []}),
        apps=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        workflows=SimpleNamespace(
            list=lambda pod_id, limit=1000: {"items": []},
            create=lambda pod_id, request: {"name": _plain(request).get("name")},
            update_graph=lambda pod_id, name, payload: recorded.append((name, payload))
            or {"name": name},
        ),
        members=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": members}),
        user=SimpleNamespace(profile=lambda: SimpleNamespace(id=profile_id)),
        files=SimpleNamespace(
            tree=lambda pod_id, root_path="/", files_per_directory=20: {
                "tree": {"path": "/", "name": "/", "kind": "FOLDER", "children": []}
            }
        ),
    )


def _assignee_in(recorded) -> str:
    assert len(recorded) == 1
    _name, payload = recorded[0]
    return payload["nodes"][0]["config"]["assignee_pod_member_id"]


def test_import_resolves_pod_member_token_to_importing_member(tmp_path: Path):
    _write_tokenized_workflow(tmp_path)
    recorded: list = []
    client = _import_client(
        recorded,
        members=[{"user_id": "user-1", "pod_member_id": "member-1", "email": "a@x.io"}],
        profile_id="user-1",
    )

    result = import_pod_bundle(client, pod_id="pod_123", source_dir=tmp_path)

    assert result["ok"] is True
    assert _assignee_in(recorded) == "member-1"  # NOT the literal "$POD_MEMBER"


def test_import_pod_member_override_takes_precedence(tmp_path: Path):
    _write_tokenized_workflow(tmp_path)
    recorded: list = []
    # No matching member + a different profile: override must still win.
    client = _import_client(recorded, members=[], profile_id="nobody")

    result = import_pod_bundle(
        client, pod_id="pod_123", source_dir=tmp_path, pod_member_id="explicit-member"
    )

    assert result["ok"] is True
    assert _assignee_in(recorded) == "explicit-member"


def test_import_fails_loudly_when_token_unresolvable(tmp_path: Path):
    _write_tokenized_workflow(tmp_path)
    recorded: list = []
    client = _import_client(
        recorded,
        members=[{"user_id": "someone-else", "pod_member_id": "member-9", "email": "b@x.io"}],
        profile_id="user-1",  # importing user is not a member
    )

    with pytest.raises(ValueError, match=r"\$POD_MEMBER|--pod-member"):
        import_pod_bundle(client, pod_id="pod_123", source_dir=tmp_path)
    assert recorded == []  # nothing imported with a bogus assignee


def test_resource_dirs_warns_on_misnamed_manifest(tmp_path: Path, monkeypatch):
    (tmp_path / "tables" / "good").mkdir(parents=True)
    (tmp_path / "tables" / "good" / "good.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tables" / "misnamed").mkdir(parents=True)
    (tmp_path / "tables" / "misnamed" / "oops.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tables" / "leftover_empty").mkdir(parents=True)

    msgs: list[str] = []
    monkeypatch.setattr(pod_bundle_module.console, "print", lambda *a, **k: msgs.append(str(a[0])))

    dirs = _resource_dirs(tmp_path, "tables")

    assert [d.name for d in dirs] == ["good"]
    assert any("misnamed" in m for m in msgs)  # non-empty + no manifest → warn
    assert not any("leftover_empty" in m for m in msgs)  # empty → silent


def test_export_then_seed_table_data_strips_audit_columns(tmp_path: Path):
    from lemma_cli.cli_app.pod_bundle import _export_table_data, _import_table_data

    resource_dir = tmp_path / "tables" / "people"
    resource_dir.mkdir(parents=True)
    src_rows = [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "name": "Ada",
            "user_id": "old-user",
            "created_at": "2020-01-01",
            "updated_at": "2021-01-01",
        },
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "name": "Lin",
            "user_id": "old-user",
            "created_at": "2020-01-01",
            "updated_at": "2021-01-01",
        },
    ]

    export_sdk = SimpleNamespace(
        records=SimpleNamespace(
            list=lambda table, *, limit, offset: {
                "items": src_rows[offset : offset + limit],
                "total": len(src_rows),
            }
        )
    )
    _export_table_data(export_sdk, "people", resource_dir)
    assert (resource_dir / "data.csv").is_file()

    captured: list[tuple[str, list[dict]]] = []
    import_sdk = SimpleNamespace(
        records=SimpleNamespace(
            bulk_create=lambda table, items, upsert=False: captured.append(
                (table, items, upsert)
            )
            or len(items)
        )
    )
    count = _import_table_data(import_sdk, "people", resource_dir)

    assert count == 2
    table, items, upsert = captured[0]
    assert table == "people"
    assert upsert is True  # idempotent re-seed
    for item in items:
        # Audit/ownership columns are dropped so the backend manages them.
        assert "user_id" not in item
        assert "created_at" not in item
        assert "updated_at" not in item
        # Primary key + business columns survive for FK integrity.
        assert "id" in item
        assert "name" in item


def test_seed_strips_auto_generated_columns_except_primary_key(tmp_path: Path):
    """Regression: a value supplied for an auto column such as a SERIAL is
    rejected by the datastore. Seeding must drop every `auto` column except the
    primary key (which is kept for upsert matching / FK integrity)."""
    from lemma_cli.cli_app.pod_bundle import _import_table_data

    resource_dir = tmp_path / "tables" / "lucky_draw"
    resource_dir.mkdir(parents=True)
    (resource_dir / "lucky_draw.json").write_text(
        json.dumps(
            {
                "name": "lucky_draw",
                "primary_key_column": "id",
                "columns": [
                    {"name": "id", "type": "UUID", "auto": True},
                    {"name": "ticket_number", "type": "SERIAL", "auto": True},
                    {"name": "nickname", "type": "TEXT", "auto": False},
                    {"name": "created_at", "type": "DATETIME", "auto": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    (resource_dir / "data.csv").write_text(
        "id,created_at,ticket_number,nickname\n"
        "11111111-1111-1111-1111-111111111111,2020-01-01,1,Ada\n",
        encoding="utf-8",
    )

    captured: list = []
    import_sdk = SimpleNamespace(
        records=SimpleNamespace(
            bulk_create=lambda table, items, upsert=False: captured.append(items)
            or len(items)
        )
    )

    count = _import_table_data(import_sdk, "lucky_draw", resource_dir)

    assert count == 1
    item = captured[0][0]
    assert "ticket_number" not in item  # auto SERIAL -> would be rejected
    assert "created_at" not in item     # auto timestamp
    assert item["id"] == "11111111-1111-1111-1111-111111111111"  # PK kept
    assert item["nickname"] == "Ada"    # business column kept


def test_with_files_export_then_import_round_trip(tmp_path: Path):
    from lemma_cli.cli_app import pod_bundle
    from lemma_cli.cli_app.pod_bundle import _export_pod_files, _import_pod_files

    items = {
        "f1": {
            "id": "f1",
            "name": "guide.md",
            "kind": "FILE",
            "visibility": "POD",
            "path": "/guide.md",
            "description": "the guide",
            "search_enabled": True,
        }
    }

    def download(_pod_id: str, path: str) -> bytes:
        assert path == "/guide.md"
        return b"hello world"

    export_client = FakeClient(files=SimpleNamespace(download=download))
    original_fetch = pod_bundle.fetch_files_index
    pod_bundle.fetch_files_index = lambda _client, _pod_id: ({None: []}, items)
    try:
        counts = _export_pod_files(
            export_client, "pod_123", tmp_path, with_files=True
        )
    finally:
        pod_bundle.fetch_files_index = original_fetch

    assert counts == {"folders": 0, "files": 1}
    assert (tmp_path / "files" / "guide.md").read_bytes() == b"hello world"
    manifest = json.loads((tmp_path / "files" / ".files.json").read_text())
    assert manifest["files"][0]["path"] == "/guide.md"

    uploads: list[dict[str, object]] = []

    def upload(
        _pod_id: str,
        *,
        file_path: str,
        directory_path: str,
        name: str,
        description=None,
        search_enabled: bool = True,
        visibility=None,
    ) -> dict[str, object]:
        uploads.append(
            {
                "name": name,
                "directory_path": directory_path,
                "description": description,
                "search_enabled": search_enabled,
                "visibility": visibility,
                "content": Path(file_path).read_bytes(),
            }
        )
        return {"path": f"{directory_path.rstrip('/')}/{name}"}

    import_client = FakeClient(files=SimpleNamespace(upload=upload))
    original_list = pod_bundle._list_pod_visible_items
    pod_bundle._list_pod_visible_items = lambda _client, _pod_id: []
    try:
        summary = _import_pod_files(
            import_client, "pod_123", tmp_path, with_files=True
        )
    finally:
        pod_bundle._list_pod_visible_items = original_list

    assert summary == ["uploaded-file:guide.md"]
    assert uploads[0]["name"] == "guide.md"
    assert uploads[0]["directory_path"] == "/"
    assert uploads[0]["description"] == "the guide"
    assert uploads[0]["search_enabled"] is True
    assert uploads[0]["content"] == b"hello world"


def test_variable_applier_resolves_and_strips_unresolved(tmp_path: Path):
    from lemma_cli.cli_app.pod_bundle import _build_variable_applier

    (tmp_path / "pod.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "variables": {
                    "approver": {"type": "pod_member"},
                    "slack_account": {"type": "account"},
                    "ghost_account": {"type": "account"},
                },
            }
        ),
        encoding="utf-8",
    )
    apply = _build_variable_applier(
        client=None,
        pod_sdk=None,
        source_dir=tmp_path,
        var_overrides={"slack_account": "acc-new"},
        member_override="member-xyz",
    )
    out = apply(
        {
            "assignee_pod_member_id": "${approver}",
            "account_id": "${slack_account}",
            "other_account": "${ghost_account}",
            "keep": "value",
        }
    )
    assert out["assignee_pod_member_id"] == "member-xyz"  # pod_member default
    assert out["account_id"] == "acc-new"  # --var override
    assert "other_account" not in out  # unresolved account -> field dropped
    assert out["keep"] == "value"


def test_variable_applier_rejects_unknown_var(tmp_path: Path):
    from lemma_cli.cli_app.pod_bundle import _build_variable_applier

    (tmp_path / "pod.json").write_text(
        json.dumps({"name": "demo", "variables": {"approver": {"type": "pod_member"}}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Unknown --var"):
        _build_variable_applier(
            client=None,
            pod_sdk=None,
            source_dir=tmp_path,
            var_overrides={"nope": "x"},
            member_override="m",
        )


def _empty_pod_client(pod_updates: list) -> "FakeClient":
    return FakeClient(
        pods=SimpleNamespace(
            update=lambda pod_id, payload: pod_updates.append((pod_id, _plain(payload)))
        ),
        tables=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        functions=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        agents=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        workflows=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        schedules=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        surfaces=SimpleNamespace(list=lambda pod_id, limit=100: {"items": []}),
        apps=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        files=SimpleNamespace(
            tree=lambda pod_id, root_path="/", files_per_directory=20: {
                "tree": {"path": "/", "name": "/", "kind": "FOLDER", "children": []}
            }
        ),
    )


def test_import_pod_bundle_does_not_rename_pod_by_default(tmp_path: Path):
    (tmp_path / "pod.json").write_text(
        json.dumps({"name": "bundle-name", "description": "from bundle"}),
        encoding="utf-8",
    )
    for name in ("tables", "functions", "agents", "workflows", "schedules", "surfaces", "apps", "files"):
        (tmp_path / name).mkdir()

    pod_updates: list = []
    import_pod_bundle(_empty_pod_client(pod_updates), pod_id="pod_123", source_dir=tmp_path)
    assert pod_updates == []  # target pod is never renamed by default

    import_pod_bundle(
        _empty_pod_client(pod_updates),
        pod_id="pod_123",
        source_dir=tmp_path,
        set_pod_meta=True,
    )
    assert pod_updates == [
        ("pod_123", {"name": "bundle-name", "description": "from bundle"})
    ]


def _unresolved_surface_bundle(tmp_path: Path) -> Path:
    """A bundle whose Slack surface needs an account variable nobody supplied."""
    (tmp_path / "pod.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "variables": {
                    "slack_account": {
                        "type": "account",
                        "platform": "slack",
                        "description": "Connector account for the slack surface",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    slack_dir = tmp_path / "surfaces" / "slack"
    slack_dir.mkdir(parents=True)
    (slack_dir / "slack.json").write_text(
        json.dumps({"name": "slack", "platform": "SLACK", "account_id": "${slack_account}"}),
        encoding="utf-8",
    )
    return tmp_path


def _surface_client(upserted: list) -> "FakeClient":
    return FakeClient(
        tables=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        functions=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        agents=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        workflows=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        schedules=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        apps=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        surfaces=SimpleNamespace(
            list=lambda pod_id, limit=100: {"items": []},
            upsert=lambda pod_id, platform, payload: upserted.append(
                (platform, _plain(payload))
            )
            or {"id": "surface_1", "platform": platform},
        ),
        files=SimpleNamespace(
            tree=lambda pod_id, root_path="/", files_per_directory=20: {
                "tree": {"path": "/", "name": "/", "kind": "FOLDER", "children": []}
            }
        ),
    )


def test_import_blocks_on_unresolved_requirement(tmp_path: Path):
    bundle = _unresolved_surface_bundle(tmp_path)
    with pytest.raises(ValueError, match="needs values|--defer"):
        import_pod_bundle(_surface_client([]), pod_id="pod_123", source_dir=bundle)


def test_import_defer_imports_and_drops_unresolved_field(tmp_path: Path):
    bundle = _unresolved_surface_bundle(tmp_path)
    upserted: list = []
    result = import_pod_bundle(
        _surface_client(upserted), pod_id="pod_123", source_dir=bundle, allow_unresolved=True
    )
    assert result["ok"] is True
    # Surface still imported, but the unresolved account_id field was dropped.
    assert len(upserted) == 1
    assert "account_id" not in upserted[0][1]


def test_import_supplying_var_clears_the_gate(tmp_path: Path):
    bundle = _unresolved_surface_bundle(tmp_path)
    upserted: list = []
    result = import_pod_bundle(
        _surface_client(upserted),
        pod_id="pod_123",
        source_dir=bundle,
        variables={"slack_account": "acc-real"},
    )
    assert result["ok"] is True
    assert upserted[0][1]["account_id"] == "acc-real"


def test_import_dry_run_surfaces_unresolved_without_blocking(tmp_path: Path):
    bundle = _unresolved_surface_bundle(tmp_path)
    result = import_pod_bundle(
        _surface_client([]), pod_id="pod_123", source_dir=bundle, dry_run=True
    )
    assert result["ok"] is True  # dry-run is informational, never blocks
    kinds = [item["kind"] for item in result["unresolved"]]
    assert kinds == ["connector"]


def test_import_plan_surfaces_destructive_table_changes(tmp_path: Path):
    (tmp_path / "pod.json").write_text(json.dumps({"name": "demo"}), encoding="utf-8")
    contacts_dir = tmp_path / "tables" / "contacts"
    contacts_dir.mkdir(parents=True)
    # Bundle no longer declares the `email` column -> dropping it loses data.
    (contacts_dir / "contacts.json").write_text(
        json.dumps(
            {
                "name": "contacts",
                "primary_key_column": "id",
                "columns": [
                    {"name": "id", "type": "text"},
                    {"name": "name", "type": "text"},
                ],
            }
        ),
        encoding="utf-8",
    )

    existing_contacts = {
        "name": "contacts",
        "primary_key_column": "id",
        "columns": [
            {"name": "id", "type": "text"},
            {"name": "name", "type": "text"},
            {"name": "email", "type": "text"},
        ],
    }
    client = FakeClient(
        tables=SimpleNamespace(
            list=lambda pod_id, limit=1000: {"items": [{"name": "contacts"}]},
            get=lambda pod_id, name: existing_contacts,
        ),
        functions=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        agents=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        workflows=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        schedules=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        surfaces=SimpleNamespace(list=lambda pod_id, limit=100: {"items": []}),
        apps=SimpleNamespace(list=lambda pod_id, limit=1000: {"items": []}),
        files=SimpleNamespace(
            tree=lambda pod_id, root_path="/", files_per_directory=20: {
                "tree": {"path": "/", "name": "/", "kind": "FOLDER", "children": []}
            }
        ),
    )

    result = import_pod_bundle(client, pod_id="pod_123", source_dir=tmp_path, dry_run=True)

    assert result["ok"] is True  # destructive change is surfaced, not an error
    assert result["destructive"] == [
        {"table": "contacts", "removed_columns": ["email"]}
    ]


def test_export_contents_manifest_roundtrip(tmp_path: Path):
    from lemma_cli.cli_app.pod_bundle import (
        _read_export_contents,
        _record_export_contents,
    )

    (tmp_path / "pod.json").write_text(json.dumps({"name": "demo"}), encoding="utf-8")
    contents = _record_export_contents(
        tmp_path,
        included={"tables", "files"},
        excluded=set(),
        names={"issues", "/docs"},
        with_data=True,
        with_files=True,
    )
    assert contents == {
        "resources": ["files", "tables"],
        "names": ["/docs", "issues"],
        "with_data": True,
        "with_files": True,
    }
    pod = json.loads((tmp_path / "pod.json").read_text(encoding="utf-8"))
    assert pod["contents"]["with_data"] is True
    # Re-readable (drives import auto-enable of data/files).
    assert _read_export_contents(tmp_path) == contents
    # Older bundle without a contents block reads as empty.
    (tmp_path / "bare").mkdir()
    (tmp_path / "bare" / "pod.json").write_text('{"name":"x"}', encoding="utf-8")
    assert _read_export_contents(tmp_path / "bare") == {}


def test_validate_grant_references_flags_dangling_grants(tmp_path: Path):
    from lemma_cli.cli_app.pod_bundle import _validate_grant_references

    agent_dir = tmp_path / "agents" / "triage"
    agent_dir.mkdir(parents=True)
    (agent_dir / "triage.json").write_text(
        json.dumps(
            {
                "name": "triage",
                "permissions": {
                    "grants": [
                        {
                            "resource_type": "datastore_table",
                            "resource_name": "issues",
                            "permission_ids": ["datastore.table.read"],
                        },
                        {
                            "resource_type": "folder",
                            "resource_name": "/docs/missing",
                            "permission_ids": ["folder.read"],
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    issues: list = []
    _validate_grant_references(
        tmp_path,
        issues,
        valid_tables={"issues"},
        valid_functions=set(),
        valid_agents=set(),
        valid_folder_keys=set(),
    )
    msgs = [i.message for i in issues]
    # The dangling folder grant is flagged; the valid table grant is not.
    assert any("/docs/missing" in m and "folder" in m for m in msgs)
    assert not any("issues" in m for m in msgs)

    # Once the folder is present (created by the bundle or the pod), it passes.
    ok: list = []
    _validate_grant_references(
        tmp_path,
        ok,
        valid_tables={"issues"},
        valid_functions=set(),
        valid_agents=set(),
        valid_folder_keys={"docs/missing"},
    )
    assert ok == []
