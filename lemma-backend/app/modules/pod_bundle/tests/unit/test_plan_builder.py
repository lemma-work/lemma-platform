"""Plan builder diff logic against a fabricated bundle + a fake pod snapshot."""

import json
from pathlib import Path

import pytest

from app.modules.pod_bundle.domain.state import StepAction, StepKind, StepStatus
from app.modules.pod_bundle.infrastructure.plan_builder import PlanBuilder


class FakeExisting:
    """In-memory :class:`ExistingResources` — the pod's current resources."""

    def __init__(
        self,
        *,
        tables=None,
        table_manifests=None,
        functions=None,
        agents=None,
        workflows=None,
        schedules=None,
        apps=None,
        surfaces=None,
    ):
        self._tables = set(tables or [])
        self._table_manifests = table_manifests or {}
        self._functions = set(functions or [])
        self._agents = set(agents or [])
        self._workflows = set(workflows or [])
        self._schedules = set(schedules or [])
        self._apps = set(apps or [])
        self._surfaces = set(surfaces or [])

    async def table_names(self):
        return self._tables

    async def table_manifest(self, name):
        return self._table_manifests.get(name)

    async def function_names(self):
        return self._functions

    async def agent_names(self):
        return self._agents

    async def workflow_names(self):
        return self._workflows

    async def schedule_names(self):
        return self._schedules

    async def app_names(self):
        return self._apps

    async def surface_names(self):
        return self._surfaces


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _table_manifest(name, columns, pk="id"):
    return {
        "name": name,
        "primary_key_column": pk,
        "columns": [{"name": c, "type": "TEXT"} for c in columns],
    }


def _build_bundle(tmp: Path, *, variables=None) -> Path:
    root = tmp / "bundle"
    _write(
        root / "pod.json",
        {"name": "CRM", "format_version": 2, "variables": variables or {}},
    )
    return root


@pytest.fixture
def tmp(tmp_path) -> Path:
    return tmp_path


async def test_create_vs_update_classification(tmp):
    root = _build_bundle(tmp)
    _write(
        root / "tables" / "leads" / "leads.json",
        _table_manifest("leads", ["id", "name"]),
    )
    _write(root / "agents" / "bot" / "bot.json", {"name": "bot"})
    _write(
        root / "functions" / "score" / "score.json", {"name": "score", "code": "x=1"}
    )

    existing = FakeExisting(
        tables={"leads"},
        table_manifests={"leads": _table_manifest("leads", ["id", "name"])},
        agents=set(),
    )
    plan = await PlanBuilder(existing).build_plan(bundle_root=root)

    by_name = {(s.kind, s.name): s for s in plan.steps}
    assert by_name[(StepKind.TABLE, "leads")].action == StepAction.UPDATE
    assert by_name[(StepKind.AGENT, "bot")].action == StepAction.CREATE
    assert by_name[(StepKind.FUNCTION, "score")].action == StepAction.CREATE
    assert plan.format_version == 2
    assert plan.bundle_name == "CRM"
    # Steps are contiguously indexed.
    assert [s.index for s in plan.steps] == list(range(len(plan.steps)))


async def test_files_produce_folder_then_file_steps(tmp):
    root = _build_bundle(tmp)
    # files/docs/.folder.json (folder) + files/docs/guide.md (file) + manifest.
    _write(root / "files" / "docs" / ".folder.json", {"visibility": "POD"})
    (root / "files" / "docs" / "guide.md").write_text("hi", encoding="utf-8")
    _write(root / "files" / ".files.json", {"files": [{"path": "/docs/guide.md"}]})

    plan = await PlanBuilder(FakeExisting()).build_plan(bundle_root=root)

    file_steps = [s for s in plan.steps if s.kind == StepKind.FILE]
    # Folder first (so it exists before the file), then the file — manifest and
    # .folder.json are layout metadata, not steps.
    assert [(s.name, s.detail.get("is_folder")) for s in file_steps] == [
        ("docs", True),
        ("docs/guide.md", False),
    ]


async def test_no_files_dir_yields_no_file_steps(tmp):
    root = _build_bundle(tmp)
    plan = await PlanBuilder(FakeExisting()).build_plan(bundle_root=root)
    assert not [s for s in plan.steps if s.kind == StepKind.FILE]


async def test_destructive_column_drop_flagged(tmp):
    root = _build_bundle(tmp)
    # Bundle table has fewer columns than the pod's live table -> a drop.
    _write(
        root / "tables" / "leads" / "leads.json",
        _table_manifest("leads", ["id", "name"]),
    )
    existing = FakeExisting(
        tables={"leads"},
        table_manifests={"leads": _table_manifest("leads", ["id", "name", "score"])},
    )
    plan = await PlanBuilder(existing).build_plan(bundle_root=root)

    step = next(s for s in plan.steps if s.kind == StepKind.TABLE)
    assert step.action == StepAction.UPDATE
    assert step.destructive is True
    assert "score" in step.detail["columns_to_remove"]
    assert any("score" in w for w in plan.warnings)


async def test_non_destructive_update_when_only_adding_columns(tmp):
    root = _build_bundle(tmp)
    _write(
        root / "tables" / "leads" / "leads.json",
        _table_manifest("leads", ["id", "name", "score"]),
    )
    existing = FakeExisting(
        tables={"leads"},
        table_manifests={"leads": _table_manifest("leads", ["id", "name"])},
    )
    plan = await PlanBuilder(existing).build_plan(bundle_root=root)

    step = next(s for s in plan.steps if s.kind == StepKind.TABLE)
    assert step.action == StepAction.UPDATE
    assert step.destructive is False
    assert "score" in step.detail["columns_to_add"]


async def test_table_data_step_emitted_after_tables(tmp):
    root = _build_bundle(tmp)
    _write(root / "tables" / "leads" / "leads.json", _table_manifest("leads", ["id"]))
    (root / "tables" / "leads" / "data.csv").write_text("id\n1\n", encoding="utf-8")

    plan = await PlanBuilder(FakeExisting()).build_plan(bundle_root=root)
    kinds = [s.kind for s in plan.steps]
    assert StepKind.TABLE in kinds and StepKind.TABLE_DATA in kinds
    # table_data comes after the table create.
    assert kinds.index(StepKind.TABLE) < kinds.index(StepKind.TABLE_DATA)


async def test_variables_classified(tmp):
    root = _build_bundle(
        tmp,
        variables={
            "acct": {
                "type": "account",
                "source_value": "x",
                "connector": "slack",
                "connector_kind": "composio",
            },
            "owner": {"type": "member", "source_value": "y"},
            "region": {"type": "string", "source_value": "z"},
            "app_slug": {"type": "app_slug", "source_value": "s", "default": "s"},
        },
    )
    plan = await PlanBuilder(FakeExisting()).build_plan(bundle_root=root)
    by_name = {v.name: v for v in plan.variables}
    # Connector accounts must be supplied by the importer -> required, and carry
    # the connector + provider so the UI can prompt for the right one.
    assert by_name["acct"].kind == "account"
    assert by_name["acct"].required is True
    assert by_name["acct"].connector == "slack"
    assert by_name["acct"].connector_kind == "composio"
    # A variable with no connector context leaves it None.
    assert by_name["region"].connector is None
    # Pod members auto-resolve to the importing user -> not required.
    assert by_name["owner"].kind == "pod_member"
    assert by_name["owner"].required is False
    # A free var with no default is required; the app slug has a default, so not.
    assert by_name["region"].kind == "free"
    assert by_name["region"].required is True
    assert by_name["app_slug"].kind == "free"
    assert by_name["app_slug"].required is False
    assert by_name["app_slug"].default == "s"


async def test_account_variable_missing_provider_is_rejected(tmp):
    """A bundle built before connector/provider was mandatory (or hand-edited)
    must be re-exported, not imported half-resolvable."""
    from app.modules.pod_bundle.domain.errors import BundleInvalidError

    root = _build_bundle(
        tmp,
        variables={
            "acct": {"type": "account", "source_value": "x", "connector": "slack"},
        },
    )
    with pytest.raises(BundleInvalidError, match="acct"):
        await PlanBuilder(FakeExisting()).build_plan(bundle_root=root)


async def test_account_variable_missing_connector_is_rejected(tmp):
    from app.modules.pod_bundle.domain.errors import BundleInvalidError

    root = _build_bundle(
        tmp,
        variables={
            "acct": {
                "type": "account",
                "source_value": "x",
                "connector_kind": "package",
            },
        },
    )
    with pytest.raises(BundleInvalidError, match="acct"):
        await PlanBuilder(FakeExisting()).build_plan(bundle_root=root)


async def test_agent_grants_step_deferred_after_resources(tmp):
    root = _build_bundle(tmp)
    _write(root / "files" / "knowledge" / ".folder.json", {"visibility": "POD"})
    _write(
        root / "workflows" / "research" / "research.json",
        {"name": "research"},
    )
    _write(
        root / "agents" / "bot" / "bot.json",
        {"name": "bot", "permissions": {"grants": [{"resource_type": "table"}]}},
    )
    plan = await PlanBuilder(FakeExisting()).build_plan(bundle_root=root)
    kinds = [s.kind for s in plan.steps]
    assert StepKind.AGENT in kinds and StepKind.AGENT_GRANTS in kinds
    assert kinds.index(StepKind.AGENT) < kinds.index(StepKind.AGENT_GRANTS)
    assert kinds.index(StepKind.WORKFLOW) < kinds.index(StepKind.AGENT_GRANTS)
    assert kinds.index(StepKind.FILE) < kinds.index(StepKind.AGENT_GRANTS)


async def test_function_grants_step_deferred_after_resources(tmp):
    """Function grants get their own late step, like agent grants.

    Applied inline at FUNCTION time (as they were), a grant naming a folder or
    another function this same bundle creates would resolve against a pod where
    neither existed yet.
    """
    root = _build_bundle(tmp)
    _write(root / "files" / "knowledge" / ".folder.json", {"visibility": "POD"})
    _write(
        root / "functions" / "rewriter" / "rewriter.json",
        {
            "name": "rewriter",
            "permissions": {
                "grants": [{"resource_type": "folder", "resource_name": "/knowledge"}]
            },
        },
    )
    plan = await PlanBuilder(FakeExisting()).build_plan(bundle_root=root)
    kinds = [s.kind for s in plan.steps]
    assert StepKind.FUNCTION in kinds and StepKind.FUNCTION_GRANTS in kinds
    assert kinds.index(StepKind.FUNCTION) < kinds.index(StepKind.FUNCTION_GRANTS)
    assert kinds.index(StepKind.FILE) < kinds.index(StepKind.FUNCTION_GRANTS)


async def test_function_without_grants_gets_no_grants_step(tmp):
    root = _build_bundle(tmp)
    _write(root / "functions" / "plain" / "plain.json", {"name": "plain"})
    plan = await PlanBuilder(FakeExisting()).build_plan(bundle_root=root)
    assert StepKind.FUNCTION_GRANTS not in [s.kind for s in plan.steps]


async def test_surface_of_a_platform_the_pod_already_has_is_still_a_create(tmp):
    """The plan is a promise about what apply will do.

    Export writes one directory per surface *name*, and `apply_surface` upserts
    by name -- so a second Slack surface is a CREATE. Diffing by platform said
    UPDATE and then created a second surface anyway."""
    root = _build_bundle(tmp)
    _write(
        root / "surfaces" / "support_bot" / "support_bot.json",
        {"name": "support_bot", "platform": "SLACK"},
    )
    plan = await PlanBuilder(FakeExisting(surfaces={"sales_bot"})).build_plan(
        bundle_root=root
    )
    step = next(s for s in plan.steps if s.kind is StepKind.SURFACE)
    assert step.action is StepAction.CREATE


async def test_surface_of_the_same_name_is_an_update(tmp):
    root = _build_bundle(tmp)
    _write(
        root / "surfaces" / "support_bot" / "support_bot.json",
        {"name": "support_bot", "platform": "SLACK"},
    )
    plan = await PlanBuilder(FakeExisting(surfaces={"support_bot"})).build_plan(
        bundle_root=root
    )
    step = next(s for s in plan.steps if s.kind is StepKind.SURFACE)
    assert step.action is StepAction.UPDATE


async def test_a_named_surface_falls_back_to_its_platform_like_the_applier(tmp):
    """A legacy manifest with no `name` upserts under the lowercased platform,
    so the diff has to resolve it the same way."""
    root = _build_bundle(tmp)
    _write(root / "surfaces" / "slack" / "slack.json", {"platform": "SLACK"})
    plan = await PlanBuilder(FakeExisting(surfaces={"slack"})).build_plan(
        bundle_root=root
    )
    step = next(s for s in plan.steps if s.kind is StepKind.SURFACE)
    assert step.action is StepAction.UPDATE


async def test_existing_workflow_and_schedule_are_planned_skip_not_update(tmp):
    """Both appliers are create-once: they return without touching an existing
    resource. A plan saying UPDATE reported every step DONE while re-importing
    an updated bundle changed nothing."""
    root = _build_bundle(tmp)
    _write(root / "workflows" / "research" / "research.json", {"name": "research"})
    _write(
        root / "schedules" / "nightly" / "nightly.json",
        {"name": "nightly", "schedule_type": "CRON"},
    )
    plan = await PlanBuilder(
        FakeExisting(workflows={"research"}, schedules={"nightly"})
    ).build_plan(bundle_root=root)

    for kind in (StepKind.WORKFLOW, StepKind.SCHEDULE):
        step = next(s for s in plan.steps if s.kind is kind)
        assert step.action is StepAction.SKIP
        # SKIPPED up front is what keeps the apply loop from running it at all:
        # `next_pending_step` hands back only PENDING/RUNNING steps.
        assert step.status is StepStatus.SKIPPED
        assert "already exists" in (step.error or "")
    assert plan.next_pending_step() is None


async def test_absent_workflow_and_schedule_are_still_creates(tmp):
    root = _build_bundle(tmp)
    _write(root / "workflows" / "research" / "research.json", {"name": "research"})
    _write(
        root / "schedules" / "nightly" / "nightly.json",
        {"name": "nightly", "schedule_type": "CRON"},
    )
    plan = await PlanBuilder(FakeExisting()).build_plan(bundle_root=root)

    for kind in (StepKind.WORKFLOW, StepKind.SCHEDULE):
        step = next(s for s in plan.steps if s.kind is kind)
        assert step.action is StepAction.CREATE
        assert step.status is StepStatus.PENDING


async def test_an_explicitly_empty_grant_list_still_gets_a_grants_step(tmp):
    """`{"grants": []}` means "this workload holds nothing" -- a write, not a
    no-op, so it needs a step for the applier to carry out.

    The plan and the applier answer this from one predicate now
    (`grants.has_grants`); they used to keep separate copies, and only the
    applier's early return decided whether the write actually happened."""
    root = _build_bundle(tmp)
    _write(
        root / "agents" / "bot" / "bot.json",
        {"name": "bot", "permissions": {"grants": []}},
    )
    plan = await PlanBuilder(FakeExisting(agents={"bot"})).build_plan(bundle_root=root)
    assert StepKind.AGENT_GRANTS in [s.kind for s in plan.steps]


async def test_an_agent_with_no_permissions_key_still_gets_no_grants_step(tmp):
    """The other half of the distinction: no `permissions` key means "leave the
    target's grants alone"."""
    root = _build_bundle(tmp)
    _write(root / "agents" / "bot" / "bot.json", {"name": "bot"})
    plan = await PlanBuilder(FakeExisting(agents={"bot"})).build_plan(bundle_root=root)
    assert StepKind.AGENT_GRANTS not in [s.kind for s in plan.steps]


async def test_the_app_step_says_how_the_app_will_be_served(tmp):
    """An imported app whose manifest omits visibility is created PUBLIC.

    That is deliberate -- an app is a shell whose data calls are authorized on
    their own -- but the plan is where the importer sees what an approval will
    do, and the APP step carried no visibility at all."""
    root = _build_bundle(tmp)
    _write(root / "apps" / "dashboard" / "dashboard.json", {"name": "dashboard"})
    _write(
        root / "apps" / "internal" / "internal.json",
        {"name": "internal", "visibility": "POD"},
    )
    plan = await PlanBuilder(FakeExisting()).build_plan(bundle_root=root)

    by_name = {s.name: s for s in plan.steps if s.kind is StepKind.APP}
    assert by_name["dashboard"].detail["visibility"] == "PUBLIC"
    assert by_name["internal"].detail["visibility"] == "POD"


async def test_an_app_the_pod_already_has_carries_no_visibility_claim(tmp):
    """Update leaves the existing app's visibility alone, so promising one
    would be the same overstatement in the other direction."""
    root = _build_bundle(tmp)
    _write(root / "apps" / "dashboard" / "dashboard.json", {"name": "dashboard"})
    plan = await PlanBuilder(FakeExisting(apps={"dashboard"})).build_plan(
        bundle_root=root
    )

    step = next(s for s in plan.steps if s.kind is StepKind.APP)
    assert step.action is StepAction.UPDATE
    assert step.detail == {}


# --- import-side caps (PS-PACK-013) ------------------------------------------


def _csv(root: Path, table: str, rows: int) -> None:
    _write(root / "tables" / table / f"{table}.json", _table_manifest(table, ["id"]))
    path = root / "tables" / table / "data.csv"
    path.write_text("id\n" + "".join(f"{i}\n" for i in range(rows)), encoding="utf-8")


async def test_a_table_seeding_more_rows_than_the_cap_is_refused_by_name(tmp):
    """The record caps were export-side only.

    They bound what we write; nothing bounded what an uploaded or GitHub-fetched
    bundle declares, and the applier reads a whole data.csv into memory before
    one bulk insert. The refusal has to name the limit that was passed."""
    from lemma_pod_bundle.limits import MAX_RECORDS_PER_TABLE

    from app.modules.pod_bundle.domain.errors import BundleInvalidError

    root = _build_bundle(tmp)
    _csv(root, "leads", MAX_RECORDS_PER_TABLE + 1)

    with pytest.raises(BundleInvalidError) as excinfo:
        await PlanBuilder(FakeExisting()).build_plan(bundle_root=root)
    assert str(MAX_RECORDS_PER_TABLE) in str(excinfo.value)
    assert "leads" in str(excinfo.value)


async def test_seed_rows_across_tables_are_bounded_in_total(tmp):
    from lemma_pod_bundle.limits import MAX_RECORDS_PER_TABLE, MAX_RECORDS_TOTAL

    from app.modules.pod_bundle.domain.errors import BundleInvalidError

    root = _build_bundle(tmp)
    # Each table is under the per-table cap; together they are over the total.
    for i in range((MAX_RECORDS_TOTAL // MAX_RECORDS_PER_TABLE) + 1):
        _csv(root, f"t{i}", MAX_RECORDS_PER_TABLE)

    with pytest.raises(BundleInvalidError) as excinfo:
        await PlanBuilder(FakeExisting()).build_plan(bundle_root=root)
    assert str(MAX_RECORDS_TOTAL) in str(excinfo.value)


async def test_a_bundle_within_the_record_caps_still_plans(tmp):
    root = _build_bundle(tmp)
    _csv(root, "leads", 3)
    plan = await PlanBuilder(FakeExisting()).build_plan(bundle_root=root)
    assert StepKind.TABLE_DATA in [s.kind for s in plan.steps]


async def test_a_bundle_declaring_more_steps_than_the_cap_is_refused(tmp):
    """500 MiB uncompressed still allows tens of thousands of tiny files, and
    every one of them was a step the importer would carry out."""
    from lemma_pod_bundle.limits import MAX_IMPORT_PLAN_STEPS

    from app.modules.pod_bundle.domain.errors import BundleInvalidError

    root = _build_bundle(tmp)
    files_root = root / "files"
    files_root.mkdir(parents=True, exist_ok=True)
    for i in range(MAX_IMPORT_PLAN_STEPS + 1):
        (files_root / f"f{i}.md").write_text("x", encoding="utf-8")

    with pytest.raises(BundleInvalidError) as excinfo:
        await PlanBuilder(FakeExisting()).build_plan(bundle_root=root)
    assert str(MAX_IMPORT_PLAN_STEPS) in str(excinfo.value)
