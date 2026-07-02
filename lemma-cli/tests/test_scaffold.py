from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lemma_cli.cli_app.pod_bundle import load_resource_payload, loads_jsonc, strip_jsonc
from lemma_cli.cli_app.scaffold import (
    ScaffoldError,
    find_bundle_root,
    grant_in_bundle,
    init_pod,
    init_resource,
    merge_grants,
    parse_grant_spec,
    pascalify,
    resolve_root,
    resource_example,
    slugify,
    snakeify,
    templatize_bundle,
)


# --------------------------------------------------------------------------- #
# JSONC
# --------------------------------------------------------------------------- #
def test_strip_jsonc_line_and_block_comments():
    text = """{
  // a line comment
  "a": 1,  // trailing comment
  "b": 2   /* block */
}"""
    assert loads_jsonc(text) == {"a": 1, "b": 2}


def test_strip_jsonc_preserves_slashes_in_strings():
    text = '{ "url": "http://example.com/a//b", "note": "1/2" }'
    assert loads_jsonc(text) == {"url": "http://example.com/a//b", "note": "1/2"}


def test_strip_jsonc_preserves_line_numbers():
    # Comment replaced in place so an error on line 3 still reports line 3.
    text = "{\n  // comment\n  bad\n}"
    stripped = strip_jsonc(text)
    assert stripped.count("\n") == text.count("\n")


def test_strip_jsonc_block_comment_with_star_and_quote():
    text = '{ "a": /* keep "this" star * slash */ 5 }'
    assert loads_jsonc(text) == {"a": 5}


# --------------------------------------------------------------------------- #
# name helpers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [("Lead Qualifier!!", "lead-qualifier"), ("triage_agent", "triage-agent"), ("A B C", "a-b-c")],
)
def test_slugify(raw, expected):
    assert slugify(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("Score The Lead", "score_the_lead"), ("my-func", "my_func"), ("X", "x")],
)
def test_snakeify(raw, expected):
    assert snakeify(raw) == expected


def test_pascalify():
    assert pascalify("score the lead") == "ScoreTheLead"
    assert pascalify("my-func") == "MyFunc"


def test_name_helpers_reject_empty():
    with pytest.raises(ScaffoldError):
        slugify("!!!")
    with pytest.raises(ScaffoldError):
        snakeify("###")


# --------------------------------------------------------------------------- #
# init_pod
# --------------------------------------------------------------------------- #
def test_init_pod_creates_parsable_bundle(tmp_path: Path):
    result = init_pod(tmp_path / "demo", "Demo Ops")
    assert result.name == "demo-ops"
    names = {p.name for p in result.files}
    assert {"pod.json", "items.json", "hello.json", "instruction.md", "README.md", "AGENTS.md"} <= names

    # Every scaffolded JSON parses as JSONC and matches the folder==name rule.
    root = tmp_path / "demo"
    assert load_resource_payload(root / "tables" / "items", "items")["name"] == "items"
    agent = load_resource_payload(root / "agents" / "hello", "hello")
    assert agent["visibility"] == "POD"
    # the starter agent is granted on the items table
    grants = agent["permissions"]["grants"]
    assert any(g["resource_name"] == "items" for g in grants)
    # instruction.md resolved via $file ref into a string
    assert isinstance(agent["instruction"], str) and agent["instruction"].strip()


def test_init_pod_refuses_existing_without_force(tmp_path: Path):
    init_pod(tmp_path / "demo", "demo")
    with pytest.raises(ScaffoldError):
        init_pod(tmp_path / "demo", "demo")
    # force overwrites
    init_pod(tmp_path / "demo", "demo", force=True)


# --------------------------------------------------------------------------- #
# init_resource
# --------------------------------------------------------------------------- #
def test_init_function_headers_match_folder(tmp_path: Path):
    result = init_resource("function", "Score The Lead", root=tmp_path)
    assert result.name == "score_the_lead"
    code = (tmp_path / "functions" / "score_the_lead" / "code.py").read_text()
    ast.parse(code)  # valid python
    header_lines = [ln for ln in code.splitlines() if ln.startswith("#")][:3]
    assert "#function_name: score_the_lead" in header_lines
    assert "#input_type_name: ScoreTheLeadInput" in header_lines


def test_init_table_shared_flag(tmp_path: Path):
    init_resource("table", "widgets", root=tmp_path, shared=True)
    shared = loads_jsonc((tmp_path / "tables" / "widgets" / "widgets.json").read_text())
    assert shared["enable_rls"] is False

    init_resource("table", "secrets", root=tmp_path)  # default = RLS on
    private = loads_jsonc((tmp_path / "tables" / "secrets" / "secrets.json").read_text())
    assert private["enable_rls"] is True


def test_init_every_resource_type_parses(tmp_path: Path):
    init_resource("table", "items", root=tmp_path)
    init_resource("function", "do_it", root=tmp_path)
    init_resource("agent", "helper", root=tmp_path)
    init_resource("workflow", "flow", root=tmp_path)
    init_resource("schedule", "nightly", root=tmp_path)
    init_resource("surface", "slack", root=tmp_path)
    for rdir, name in [
        ("tables/items", "items"),
        ("functions/do_it", "do_it"),
        ("agents/helper", "helper"),
        ("workflows/flow", "flow"),
        ("schedules/nightly", "nightly"),
        ("surfaces/slack", "slack"),
    ]:
        payload = load_resource_payload(tmp_path / rdir, name)
        assert payload  # parsed, non-empty
        # visibility is a first-class bundle field on the resources that carry it
        if name not in {"slack"}:
            assert payload.get("visibility") == "POD"


def test_init_workflow_graph_is_valid_shape(tmp_path: Path):
    init_resource("workflow", "flow", root=tmp_path)
    wf = load_resource_payload(tmp_path / "workflows" / "flow", "flow")
    node_ids = {n["id"] for n in wf["nodes"]}
    assert {"intake", "end"} <= node_ids
    for edge in wf["edges"]:
        assert edge["source"] in node_ids and edge["target"] in node_ids


def test_init_unknown_resource_type(tmp_path: Path):
    with pytest.raises(ScaffoldError):
        init_resource("gadget", "x", root=tmp_path)


# --------------------------------------------------------------------------- #
# bundle root resolution
# --------------------------------------------------------------------------- #
def test_find_bundle_root_walks_up(tmp_path: Path):
    init_pod(tmp_path / "demo", "demo")
    nested = tmp_path / "demo" / "agents" / "assistant"
    assert find_bundle_root(nested) == (tmp_path / "demo").resolve()


def test_find_bundle_root_none_when_absent(tmp_path: Path):
    assert find_bundle_root(tmp_path) is None


def test_resolve_root_prefers_explicit(tmp_path: Path):
    explicit = tmp_path / "x"
    assert resolve_root(explicit) == explicit


# --------------------------------------------------------------------------- #
# grants (X7)
# --------------------------------------------------------------------------- #
def test_parse_grant_spec_table_inferred():
    g = parse_grant_spec("tickets:read,write")
    assert g["resource_type"] == "datastore_table"
    assert g["resource_name"] == "tickets"
    assert g["permission_ids"] == [
        "datastore.table.read",
        "datastore.record.read",
        "datastore.record.write",
    ]


def test_parse_grant_spec_folder_inferred_by_slash():
    g = parse_grant_spec("/knowledge:read")
    assert g["resource_type"] == "folder"
    assert g["permission_ids"] == ["folder.read"]


def test_parse_grant_spec_explicit_app_and_raw_perm():
    g = parse_grant_spec("app:gmail:use")
    assert g["resource_type"] == "connector"
    assert g["permission_ids"] == ["connector.use"]
    raw = parse_grant_spec("table:tickets:datastore.record.write")
    assert raw["permission_ids"] == ["datastore.record.write"]


def test_parse_grant_spec_table_delete_is_structural():
    # Regression: the delete preset used to emit datastore.record.delete, a
    # permission id that does not exist in the backend registry (grants with it
    # were rejected server-side). Record deletion rides on datastore.record.write;
    # `delete` means the structural (destructive) table delete.
    g = parse_grant_spec("tickets:delete")
    assert g["permission_ids"] == ["datastore.table.delete"]


def test_parse_grant_spec_function_and_agent_tools():
    # Functions/agents can be granted as tools; execute is self-sufficient.
    fn = parse_grant_spec("function:cycle_velocity:execute")
    assert fn["resource_type"] == "function"
    assert fn["resource_name"] == "cycle_velocity"
    assert fn["permission_ids"] == ["function.execute"]

    ag = parse_grant_spec("agent:triage:execute")
    assert ag["resource_type"] == "agent"
    assert ag["permission_ids"] == ["agent.execute"]

    # One token per verb: `use` is for connector apps, not functions/agents.
    with pytest.raises(ScaffoldError):
        parse_grant_spec("function:cycle_velocity:use")


def test_parse_grant_spec_rejects_bad_input():
    with pytest.raises(ScaffoldError):
        parse_grant_spec("tickets")  # no perms
    with pytest.raises(ScaffoldError):
        parse_grant_spec("tickets:bogus")  # unknown perm token


def test_merge_grants_unions_permission_ids():
    existing = [{"resource_type": "datastore_table", "resource_name": "t", "permission_ids": ["a"]}]
    new = [{"resource_type": "datastore_table", "resource_name": "t", "permission_ids": ["a", "b"]}]
    merged = merge_grants(existing, new)
    assert len(merged) == 1
    assert merged[0]["permission_ids"] == ["a", "b"]


def test_grant_in_bundle_merges_into_file(tmp_path: Path):
    init_resource("agent", "triage", root=tmp_path)
    path, perms = grant_in_bundle("agent", "triage", ["tickets:read", "/kb:read"], root=tmp_path)
    names = {g["resource_name"] for g in perms["grants"]}
    assert {"tickets", "/kb"} <= names
    # second call unions, not duplicates
    _, perms2 = grant_in_bundle("agent", "triage", ["tickets:write"], root=tmp_path)
    tickets = [g for g in perms2["grants"] if g["resource_name"] == "tickets"][0]
    assert "datastore.record.write" in tickets["permission_ids"]
    assert sum(1 for g in perms2["grants"] if g["resource_name"] == "tickets") == 1


def test_grant_in_bundle_requires_scaffold(tmp_path: Path):
    with pytest.raises(ScaffoldError):
        grant_in_bundle("agent", "missing", ["t:read"], root=tmp_path)


# --------------------------------------------------------------------------- #
# runtime pin (AG2) + schema (X10) + templatize (P3)
# --------------------------------------------------------------------------- #
def test_init_agent_with_runtime(tmp_path: Path):
    init_resource("agent", "pinned", root=tmp_path, runtime="profile-123")
    from lemma_cli.cli_app.pod_bundle import loads_jsonc

    data = loads_jsonc((tmp_path / "agents" / "pinned" / "pinned.json").read_text())
    assert data["agent_runtime"] == {"profile_id": "profile-123"}


def test_resource_example_parses_for_each_type():
    from lemma_cli.cli_app.pod_bundle import loads_jsonc

    for rt in ["pod", "table", "function", "agent", "workflow", "schedule", "surface"]:
        assert loads_jsonc(resource_example(rt))  # valid JSONC, non-empty


def test_validate_workflow_accepts_scaffold(tmp_path: Path):
    from lemma_cli.cli_app.pod_bundle import loads_jsonc
    from lemma_cli.cli_app.scaffold import validate_workflow

    init_resource("workflow", "flow", root=tmp_path)
    wf = loads_jsonc((tmp_path / "workflows" / "flow" / "flow.json").read_text())
    assert validate_workflow(wf) == []


def test_validate_workflow_flags_problems():
    from lemma_cli.cli_app.scaffold import validate_workflow

    issues = validate_workflow({
        "nodes": [{"id": "a", "type": "FORM"}, {"id": "b", "type": "AGENT", "config": {}}],
        "edges": [{"source": "a", "target": "ZZZ"}],
    })
    joined = " ".join(issues)
    assert "ZZZ" in joined          # bad edge target
    assert "END" in joined          # missing END
    assert "agent_name" in joined   # AGENT node without target


def test_validate_workflow_flags_unknown_start_namespace():
    from lemma_cli.cli_app.scaffold import validate_workflow

    issues = validate_workflow({
        "nodes": [
            {
                "id": "load",
                "type": "FUNCTION",
                "config": {
                    "function_name": "f",
                    "input_mapping": {
                        "x": {"type": "expression", "value": "start.inputs.item_id || start.id"}
                    },
                },
            },
            {"id": "end", "type": "END"},
        ],
        "edges": [{"source": "load", "target": "end"}],
    })
    joined = " ".join(issues)
    assert "start.inputs" in joined  # the classic footgun
    assert "start.id" in joined      # invented bare key
    assert "payload" in joined       # names the valid namespaces


def test_validate_workflow_accepts_valid_start_namespaces():
    from lemma_cli.cli_app.scaffold import validate_workflow

    issues = validate_workflow({
        "nodes": [
            {
                "id": "load",
                "type": "FUNCTION",
                "config": {
                    "function_name": "f",
                    "input_mapping": {
                        "x": {"type": "expression", "value": "start.payload.item_id || start.metadata.id"}
                    },
                },
            },
            {"id": "end", "type": "END"},
        ],
        "edges": [{"source": "load", "target": "end"}],
    })
    assert not [i for i in issues if "start" in i]


# --------------------------------------------------------------------------- #
# validate_workflow: required start.config (server 422 parity)
# --------------------------------------------------------------------------- #
def _triggered_workflow(start: dict | None) -> dict:
    """A minimal valid graph (entry FUNCTION -> END) with a given start block."""
    wf = {
        "nodes": [
            {"id": "load", "type": "FUNCTION", "config": {"function_name": "f"}},
            {"id": "end", "type": "END"},
        ],
        "edges": [{"id": "e1", "source": "load", "target": "end"}],
    }
    if start is not None:
        wf["start"] = start
    return wf


def test_validate_workflow_flags_datastore_start_missing_config():
    from lemma_cli.cli_app.scaffold import validate_workflow

    issues = validate_workflow(_triggered_workflow({"type": "DATASTORE_EVENT"}))
    joined = " ".join(issues)
    assert "DATASTORE_EVENT" in joined
    assert "config" in joined
    assert "table_name" in joined  # names the required field the server wants


def test_validate_workflow_flags_scheduled_start_missing_inner_field():
    from lemma_cli.cli_app.scaffold import validate_workflow

    # config present but missing schedule_type -> server 422s on the inner field.
    issues = validate_workflow(
        _triggered_workflow({"type": "SCHEDULED", "config": {}})
    )
    joined = " ".join(issues)
    assert "SCHEDULED" in joined
    assert "schedule_type" in joined


def test_validate_workflow_flags_event_start_missing_fields():
    from lemma_cli.cli_app.scaffold import validate_workflow

    issues = validate_workflow(
        _triggered_workflow(
            {"type": "EVENT", "config": {"connector_id": "gmail"}}
        )
    )
    # connector_id is supplied; only connector_trigger_id is missing.
    assert any("connector_trigger_id" in i and "missing" in i for i in issues)
    assert not any("connector_id" in i and "missing" in i for i in issues)


def test_validate_workflow_flags_bad_schedule_type():
    from lemma_cli.cli_app.scaffold import validate_workflow

    issues = validate_workflow(
        _triggered_workflow(
            {"type": "SCHEDULED", "config": {"schedule_type": "HOURLY"}}
        )
    )
    joined = " ".join(issues)
    assert "schedule_type" in joined
    assert "ONCE" in joined and "CRON" in joined


def test_validate_workflow_flags_bad_datastore_operation():
    from lemma_cli.cli_app.scaffold import validate_workflow

    issues = validate_workflow(
        _triggered_workflow(
            {
                "type": "DATASTORE_EVENT",
                "config": {"table_name": "expenses", "operations": ["INSERT", "UPSERT"]},
            }
        )
    )
    joined = " ".join(issues)
    assert "operations" in joined
    assert "UPSERT" in joined


def test_validate_workflow_accepts_valid_triggered_starts():
    from lemma_cli.cli_app.scaffold import validate_workflow

    valid_starts = [
        {"type": "MANUAL"},
        None,  # no start block -> treated as manual
        {"type": "SCHEDULED", "config": {"schedule_type": "CRON"}},
        {
            "type": "DATASTORE_EVENT",
            "config": {"table_name": "expenses", "operations": ["INSERT", "UPDATE"]},
        },
        {
            "type": "EVENT",
            "config": {"connector_trigger_id": "t1", "connector_id": "gmail"},
        },
    ]
    for start in valid_starts:
        issues = validate_workflow(_triggered_workflow(start))
        assert [i for i in issues if "start" in i.lower() and "config" in i] == [], (
            f"unexpected start.config issue for {start}: {issues}"
        )


# --------------------------------------------------------------------------- #
# validate_workflow: DECISION silent-misroute
# --------------------------------------------------------------------------- #
def test_validate_workflow_flags_decision_silent_misroute():
    from lemma_cli.cli_app.scaffold import validate_workflow

    # The dogfood footgun: a rejection (no rule match) falls through to the
    # first outgoing edge, which points at the SAME node the 'approve' rule
    # targets -> a rejection is silently treated as an approval.
    issues = validate_workflow({
        "start": {"type": "MANUAL"},
        "nodes": [
            {"id": "intake", "type": "FORM",
             "config": {"input_schema": {"type": "object"}}},
            {"id": "decide", "type": "DECISION",
             "config": {"rules": [
                 {"condition": "intake.decision == 'approved'", "next_node_id": "approve"},
             ]}},
            {"id": "approve", "type": "END"},
            {"id": "reject", "type": "END"},
        ],
        "edges": [
            {"id": "e0", "source": "intake", "target": "decide"},
            # First outgoing edge from the decision is the implicit default and
            # points at 'approve' — the same target as the approve rule.
            {"id": "e1", "source": "decide", "target": "approve"},
        ],
    })
    joined = " ".join(issues)
    assert "decide" in joined
    assert "approve" in joined          # names the mis-routed default target
    assert "intake.decision == 'approved'" in joined  # names the culprit rule


def test_validate_workflow_accepts_decision_with_explicit_branches():
    from lemma_cli.cli_app.scaffold import validate_workflow

    # Every case has its own rule; the default edge is a distinct catch-all
    # handler no rule targets -> no silent same-target misroute.
    issues = validate_workflow({
        "start": {"type": "MANUAL"},
        "nodes": [
            {"id": "intake", "type": "FORM",
             "config": {"input_schema": {"type": "object"}}},
            {"id": "decide", "type": "DECISION",
             "config": {"rules": [
                 {"condition": "intake.decision == 'approved'", "next_node_id": "approve"},
                 {"condition": "intake.decision == 'rejected'", "next_node_id": "reject"},
             ]}},
            {"id": "approve", "type": "END"},
            {"id": "reject", "type": "END"},
            {"id": "needs_review", "type": "END"},
        ],
        "edges": [
            {"id": "e0", "source": "intake", "target": "decide"},
            {"id": "e1", "source": "decide", "target": "needs_review"},
        ],
    })
    # The default edge points at a distinct catch-all -> no same-target misroute.
    assert not [i for i in issues if "silently routes" in i]


def test_validate_workflow_flags_decision_with_no_rules():
    from lemma_cli.cli_app.scaffold import validate_workflow

    issues = validate_workflow({
        "start": {"type": "MANUAL"},
        "nodes": [
            {"id": "decide", "type": "DECISION", "config": {"rules": []}},
            {"id": "end", "type": "END"},
        ],
        "edges": [{"id": "e1", "source": "decide", "target": "end"}],
    })
    joined = " ".join(issues)
    assert "decide" in joined
    assert "no rules" in joined


def test_extract_portable_variables_tokenizes_assignee_member_id(tmp_path: Path):
    import json

    from lemma_cli.cli_app.pod_bundle import _extract_portable_variables
    from lemma_cli.cli_app.scaffold import substitute_placeholders

    (tmp_path / "pod.json").write_text(json.dumps({"name": "demo"}))
    wf_dir = tmp_path / "workflows" / "approve"
    wf_dir.mkdir(parents=True)
    wf = {
        "nodes": [
            {
                "id": "ask",
                "type": "FORM",
                "config": {"assignee_pod_member_id": "019ebadc-d86a-7424-9221-e3424f05b1a6"},
            }
        ]
    }
    (wf_dir / "approve.json").write_text(json.dumps(wf))

    variables = _extract_portable_variables(tmp_path)
    assert variables == {
        "approve_assignee": {
            "type": "pod_member",
            "source_value": "019ebadc-d86a-7424-9221-e3424f05b1a6",
            "description": "Pod member assigned in workflow 'approve'",
        }
    }
    out = json.loads((wf_dir / "approve.json").read_text())
    assert out["nodes"][0]["config"]["assignee_pod_member_id"] == "${approve_assignee}"
    # The variable is recorded in pod.json for the importer to resolve.
    pod_data = json.loads((tmp_path / "pod.json").read_text())
    assert "approve_assignee" in pod_data["variables"]

    # The importer resolves the placeholder to a real member id.
    resolved = substitute_placeholders(out, {"${approve_assignee}": "member-123"})
    assert resolved["nodes"][0]["config"]["assignee_pod_member_id"] == "member-123"


def test_humanize_error_keyerror():
    from lemma_cli.cli_core.state import humanize_error

    assert humanize_error(KeyError("instruction")) == "Missing required field: instruction."


def test_humanize_error_api_validation():
    from lemma_sdk.errors import LemmaAPIError
    from lemma_cli.cli_core.state import humanize_error

    exc = LemmaAPIError(
        status_code=422,
        message="Validation error",
        code="VALIDATION",
        details=[{"loc": ["body", "name"], "msg": "field required"}],
    )
    out = humanize_error(exc)
    assert "name: field required" in out


def test_templatize_bundle_strips_runtime(tmp_path: Path):
    init_pod(tmp_path / "p", "p")
    # pin a runtime, then templatize it away
    init_resource("agent", "pinned", root=tmp_path / "p", runtime="profile-xyz")
    root, changed = templatize_bundle(tmp_path / "p")
    from lemma_cli.cli_app.pod_bundle import loads_jsonc

    data = loads_jsonc((root / "agents" / "pinned" / "pinned.json").read_text())
    assert "agent_runtime" not in data
    assert changed == 1


# --------------------------------------------------------------------------- #
# single-source enums (sourced from the generated SDK models)
# --------------------------------------------------------------------------- #
def test_enums_match_sdk_source():
    from lemma_cli.cli_app import enums
    from lemma_sdk.openapi_client.models.agent_toolset import AgentToolset
    from lemma_sdk.openapi_client.models.datastore_data_type import DatastoreDataType
    from lemma_sdk.openapi_client.models.resource_visibility import ResourceVisibility
    from lemma_sdk.openapi_client.models.surface_platform import SurfacePlatform

    assert enums.VISIBILITY_VALUES == tuple(v.value for v in ResourceVisibility)
    assert enums.TOOLSETS == tuple(v.value for v in AgentToolset)
    assert enums.COLUMN_TYPES == tuple(v.value for v in DatastoreDataType)
    assert enums.SURFACE_PLATFORMS == tuple(v.value for v in SurfacePlatform)


def test_scaffold_comments_list_every_enum_value(tmp_path: Path):
    """The scaffold comments are rendered from the enum tuples, so every member
    of each enum appears — proving there's no hand-copied (driftable) list."""
    from lemma_cli.cli_app import enums

    table = (init_resource("table", "items", root=tmp_path).files[0]).read_text()
    for col_type in enums.COLUMN_TYPES:
        assert col_type in table
    agent = (init_resource("agent", "helper", root=tmp_path).files[0]).read_text()
    for toolset in enums.TOOLSETS:
        assert toolset in agent
    surface = (init_resource("surface", "slack", root=tmp_path).files[0]).read_text()
    for platform in enums.SURFACE_PLATFORMS:
        assert platform in surface


# --------------------------------------------------------------------------- #
# JSONC trailing commas (loads_jsonc tolerance)
# --------------------------------------------------------------------------- #
def test_loads_jsonc_tolerates_trailing_commas():
    assert loads_jsonc('{"a": 1,}') == {"a": 1}
    assert loads_jsonc('{"x": [1, 2, ]}') == {"x": [1, 2]}
    assert loads_jsonc('{"a": 1, "b": [3,],}') == {"a": 1, "b": [3]}


def test_loads_jsonc_keeps_commas_inside_strings():
    assert loads_jsonc('{"a": "1,2,]"}') == {"a": "1,2,]"}


def test_strip_trailing_commas_preserves_offsets():
    from lemma_cli.cli_app.pod_bundle import _strip_trailing_commas

    text = '{\n  "a": 1,\n}'
    stripped = _strip_trailing_commas(text)
    assert len(stripped) == len(text)
    assert stripped.count("\n") == text.count("\n")


# --------------------------------------------------------------------------- #
# grant: raw permission ids + comment preservation
# --------------------------------------------------------------------------- #
def test_parse_grant_spec_accepts_raw_folder_permission_id():
    g = parse_grant_spec("/kb:folder.read")
    assert g["resource_type"] == "folder"
    assert g["permission_ids"] == ["folder.read"]


def test_grant_in_bundle_preserves_jsonc_comments(tmp_path: Path):
    init_resource("agent", "triage", root=tmp_path)
    json_path = tmp_path / "agents" / "triage" / "triage.json"
    before = json_path.read_text()
    assert "// toolsets:" in before  # sanity: scaffold has guidance comments

    path, perms = grant_in_bundle(
        "agent", "triage", ["tickets:read,write", "/kb:read"], root=tmp_path
    )
    after = path.read_text()
    # comments OUTSIDE the grants array survive the edit
    assert "// toolsets:" in after
    assert "PERSONAL | POD" in after
    # the grants actually merged, and the file is still valid JSONC
    parsed = loads_jsonc(after)
    names = {g["resource_name"] for g in parsed["permissions"]["grants"]}
    assert {"tickets", "/kb"} <= names
    assert names == {g["resource_name"] for g in perms["grants"]}


def test_splice_grants_falls_back_when_no_array():
    from lemma_cli.cli_app.scaffold import splice_grants

    assert splice_grants('{"name": "x"}', [{"resource_type": "folder"}]) is None


def test_build_request_humanizes_missing_field():
    from lemma_cli.cli_core.payload import build_request
    from lemma_sdk.openapi_client.models.create_agent_request import CreateAgentRequest

    with pytest.raises(ValueError, match="Missing required field: instruction."):
        build_request(CreateAgentRequest, {"name": "x"})
    with pytest.raises(ValueError, match=r"\(agent triage\)"):
        build_request(CreateAgentRequest, {"name": "x"}, context="agent triage")
