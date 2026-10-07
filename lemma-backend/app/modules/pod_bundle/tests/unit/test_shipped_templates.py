"""Every template in ``templates/`` imports cleanly into an empty pod.

A template is applied when someone hires a teammate, with nobody reading the
plan first, so a mistake in one is found by the person hiring. And the import
pipeline is forgiving in exactly the places an author slips: a BOOLEAN cell that
is not ``true``/``false`` loads as false, and a SKILL.md with a bad frontmatter
is skipped by the pod's skill catalog without a word. The checks here are
therefore stricter than the pipeline, and run against what actually ships --
packed and extracted the way the plan job sees it.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest
from lemma_pod_bundle import (
    FORMAT_VERSION,
    TABLE_DATA_FILE,
    extract_bundle,
    load_resource_payload,
)

from app.modules.agent.domain.scorecard import (
    CounterName,
    Measure,
    measure_from_row,
)
from app.modules.agent.domain.scorecard_work import (
    UnitTable,
    check_identifier,
    work_count_sql,
    work_rows_sql,
    work_spec,
)
from app.modules.datastore.contracts import ColumnSchema
from app.modules.datastore.services.sql_introspection import analyze_query
from app.modules.datastore.contracts.agent_tools import configured_system_skills_root
from app.modules.datastore.services.value_converter import ValueConverter
from app.modules.pod_bundle.domain.state import ImportPlan, StepAction, StepKind
from app.modules.pod_bundle.infrastructure.applier import (
    _file_manifest_entry,
    _is_system_column,
    _read_csv,
    _read_json_file,
)
from app.modules.pod_bundle.infrastructure.plan_builder import PlanBuilder
from app.modules.pod_bundle.infrastructure.templates import (
    TEMPLATES_ROOT,
    pack_template,
    shipped_template_names,
)

TEMPLATES = shipped_template_names()

# skill_loader's rule for a skill name.
_SKILL_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


class _EmptyPod:
    """A pod with nothing in it yet -- what a hire imports into."""

    async def table_names(self) -> set[str]:
        return set()

    async def table_manifest(self, name: str) -> None:
        return None

    async def function_names(self) -> set[str]:
        return set()

    async def agent_names(self) -> set[str]:
        return set()

    async def workflow_names(self) -> set[str]:
        return set()

    async def schedule_names(self) -> set[str]:
        return set()

    async def app_names(self) -> set[str]:
        return set()

    async def surface_names(self) -> set[str]:
        return set()


@pytest.fixture(params=TEMPLATES)
def bundle_root(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    return extract_bundle(pack_template(request.param), tmp_path)


async def _plan(bundle_root: Path) -> ImportPlan:
    return await PlanBuilder(_EmptyPod()).build_plan(bundle_root=bundle_root)


def _tables(bundle_root: Path) -> list[tuple[str, dict[str, object]]]:
    tables_dir = bundle_root / "tables"
    if not tables_dir.is_dir():
        return []
    return [
        (path.name, load_resource_payload(path, path.name, resource_type="tables"))
        for path in sorted(tables_dir.iterdir())
        if path.is_dir()
    ]


def _columns(payload: dict[str, object]) -> dict[str, ColumnSchema]:
    """The columns the applier creates: validated the same way, system ones
    left for the datastore to add."""
    declared = payload.get("columns")
    assert isinstance(declared, list)
    columns = [
        ColumnSchema.model_validate(column)
        for column in declared
        if not _is_system_column(column)
    ]
    names = [column.name for column in columns]
    assert len(names) == len(set(names)), f"duplicate columns in {names}"
    return {column.name: column for column in columns}


def _frontmatter(text: str) -> dict[str, str]:
    """Mirrors ``skill_loader._parse_frontmatter``: a leading ``---`` block of
    top-level ``key: value`` lines, quotes stripped, indented lines ignored."""
    assert text.startswith("---\n"), "SKILL.md must open with '---'"
    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    assert end is not None, "SKILL.md frontmatter is not closed with '---'"
    fields: dict[str, str] = {}
    for raw in lines[1:end]:
        line = raw.strip()
        if not line or line.startswith("#") or raw.startswith((" ", "\t")):
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip().strip('"').strip("'")
    return fields


def _system_skill_names() -> set[str]:
    root = configured_system_skills_root()
    if root is None or not root.is_dir():
        return set()
    return {path.name for path in root.iterdir() if path.is_dir()}


def test_every_template_directory_is_importable_by_its_name():
    """A directory without a ``pod.json``, or with a name the request pattern
    refuses, would simply not be offered -- and the tests below, parametrised
    over what is offered, would never look at it."""
    directories = sorted(
        path.name for path in TEMPLATES_ROOT.iterdir() if path.is_dir()
    )
    assert directories, "no templates ship"
    assert TEMPLATES == directories


async def test_template_plans_into_an_empty_pod_without_asking_anything(
    bundle_root: Path,
):
    """A hire applies the plan with no variables and nobody confirming, so a
    template may declare nothing that must be answered, and into a new pod
    every step creates."""
    plan = await _plan(bundle_root)

    assert plan.format_version == FORMAT_VERSION
    assert plan.bundle_name
    assert plan.description
    assert [v.name for v in plan.variables if v.required] == []
    assert {step.action for step in plan.steps} == {StepAction.CREATE}
    assert not plan.has_destructive_steps
    assert plan.warnings == []


def test_template_tables_are_tables_the_datastore_accepts(bundle_root: Path):
    for name, payload in _tables(bundle_root):
        assert all(c.isalnum() or c == "_" for c in name), name
        assert payload.get("name") == name, f"{name}: manifest names another table"
        columns = _columns(payload)
        primary_key = str(payload.get("primary_key_column") or "id")
        assert primary_key in columns, f"{name}: no primary key column"
        assert isinstance(payload.get("enable_rls", True), bool), name


def test_template_seed_rows_load_into_their_column_types(bundle_root: Path):
    """Every cell converts the way the datastore converts it on write.

    Two rules go beyond the converter. A BOOLEAN cell must say ``true`` or
    ``false``, because the converter reads any other word as false. And every
    row carries its primary key: TABLE_DATA upserts by it, and the step commits
    before it is checkpointed, so a step replayed after a crash would insert
    keyless rows a second time -- or, with a unique column, fail the import.
    """
    for name, payload in _tables(bundle_root):
        data = bundle_root / "tables" / name / TABLE_DATA_FILE
        if not data.is_file():
            continue
        columns = _columns(payload)
        primary_key = str(payload.get("primary_key_column") or "id")
        rows = _read_csv(data)
        assert rows, f"{name}: {TABLE_DATA_FILE} has no rows"

        for line, row in enumerate(rows, start=2):
            where = f"{name}/{TABLE_DATA_FILE} line {line}"
            unknown = set(row) - set(columns)
            assert not unknown, f"{where}: not a column: {sorted(unknown)}"
            assert row.get(primary_key), f"{where}: no {primary_key}"
            for column in columns.values():
                cell = row.get(column.name)
                if column.required and not column.auto and column.default is None:
                    assert cell is not None, f"{where}: {column.name} is required"
                if column.type == "BOOLEAN" and cell is not None:
                    assert cell in {"true", "false"}, f"{where}: {column.name}={cell!r}"

            converted = ValueConverter.convert_record(
                row, list(columns.values()), skip_auto=False
            )
            for column in columns.values():
                value = converted.get(column.name)
                if value is None:
                    continue
                if column.type == "BOOLEAN":
                    assert isinstance(value, bool), where
                elif column.type == "FLOAT":
                    assert isinstance(value, float), where
                elif column.type == "INTEGER":
                    assert isinstance(value, int), where

        for column in columns.values():
            if column.unique:
                values = [row.get(column.name) for row in rows]
                assert len(values) == len(set(values)), f"{name}.{column.name}"


def test_template_skills_are_ones_the_skill_catalog_will_list(bundle_root: Path):
    """The pod's catalog skips a skill whose frontmatter it cannot parse rather
    than reporting it, and a name the system skills already use is read-only
    under ``/skills``, so the template's file would never be written."""
    skills_dir = bundle_root / "files" / "skills"
    if not skills_dir.is_dir():
        return
    taken = _system_skill_names()
    for skill_dir in sorted(path for path in skills_dir.iterdir() if path.is_dir()):
        skill_md = skill_dir / "SKILL.md"
        assert skill_md.is_file(), f"{skill_dir.name}: no SKILL.md"
        fields = _frontmatter(skill_md.read_text(encoding="utf-8"))
        name = fields.get("name")
        assert name == skill_dir.name, f"{skill_dir.name}: frontmatter name {name!r}"
        assert _SKILL_NAME.match(name) and "--" not in name, name
        assert fields.get("description"), f"{name}: no description"
        assert name not in taken, f"{name}: a system skill already has this name"


async def test_template_files_land_where_the_pod_shares_them(bundle_root: Path):
    """A skill is found at ``/skills/<name>/SKILL.md``, and the applier gives a
    file the visibility its metadata names, POD when it names none."""
    plan = await _plan(bundle_root)
    files_root = bundle_root / "files"
    file_steps = [step for step in plan.steps if step.kind is StepKind.FILE]
    written = {step.name for step in file_steps if not step.detail.get("is_folder")}

    skills_dir = files_root / "skills"
    if skills_dir.is_dir():
        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir():
                assert f"skills/{skill_dir.name}/SKILL.md" in written

    warnings: list[str] = []
    for step in file_steps:
        if step.detail.get("is_folder"):
            meta = _read_json_file(
                files_root / step.name / ".folder.json",
                label=step.name,
                warnings=warnings,
            )
        else:
            meta = _file_manifest_entry(files_root, f"/{step.name}", warnings)
        assert (meta.get("visibility") or "POD") == "POD", step.name
    assert warnings == []


def _unit_table(name: str, payload: dict[str, object]) -> UnitTable:
    """The table as the datastore will describe it once created: the declared
    columns, plus the timestamps it adds to every table."""
    columns = {column.name: column.type.value for column in _columns(payload).values()}
    columns.setdefault("created_at", "DATETIME")
    columns.setdefault("updated_at", "DATETIME")
    return UnitTable(
        name=name,
        primary_key=str(payload.get("primary_key_column") or "id"),
        columns=columns,
    )


def test_template_work_measures_count_columns_their_tables_have(bundle_root: Path):
    """A work measure that names a column its table lacks is refused at count
    time, so a hire would open a scorecard whose numbers all read "could not
    count". Checked here with the scorecard's own guards, and the statement it
    builds handed to the datastore's parser, which is what will run it."""
    scorecard = bundle_root / "tables" / "scorecard" / TABLE_DATA_FILE
    if not scorecard.is_file():
        return
    tables = dict(_tables(bundle_root))
    for line, row in enumerate(_read_csv(scorecard), start=2):
        measure = measure_from_row(row)
        where = f"scorecard/{TABLE_DATA_FILE} line {line}"
        assert isinstance(measure, Measure), f"{where}: {measure}"
        if measure.counter != CounterName.WORK:
            continue
        name = check_identifier("unit_table", measure.work.unit_table)
        assert name in tables, f"{where}: no table `{name}` in the template"
        spec = work_spec(measure.work, measure.shape, _unit_table(name, tables[name]))
        start, end = date(2026, 1, 5), date(2026, 1, 12)
        for sql in (
            work_count_sql(spec, start, end),
            work_rows_sql(spec, start, end, 50),
        ):
            assert analyze_query(sql).tables == frozenset({name}), where


def test_every_shipped_template_is_a_role_on_the_shelf():
    """The shelf is these cards. A template whose card stopped parsing would
    drop off it with only a log line, so the check is that none do."""
    from app.modules.pod_bundle.infrastructure.template_cards import (
        ROLE_KEY,
        RoleBlock,
        template_cards,
    )

    for name in TEMPLATES:
        manifest = json.loads((TEMPLATES_ROOT / name / "pod.json").read_text())
        RoleBlock.model_validate(manifest[ROLE_KEY])
    cards = template_cards()
    assert [card.template for card in cards] != []
    assert sorted(card.template for card in cards) == TEMPLATES
    assert len({card.seed for card in cards}) == len(cards), "two roles share a face"
    assert len({card.position for card in cards}) == len(cards), (
        "shelf order is ambiguous"
    )


def test_a_role_card_promises_only_what_its_template_brings():
    """What a card says it is judged on is its scorecard, and every skill an
    offer tells the teammate to use is one the template ships: an offer turned
    on after the hire would otherwise send it looking for a skill it lacks."""
    from app.modules.pod_bundle.infrastructure.template_cards import template_cards

    for card in template_cards():
        assert card.judged_on, f"{card.template}: judged on nothing of its own"
        assert card.about, f"{card.template}: no description"
        skills = {skill.name for skill in card.skills}
        for offer in card.offers:
            for named in re.findall(r"the ([a-z0-9-]+) skill", offer.instruction):
                assert named in skills, (
                    f"{card.template}/{offer.title}: no skill {named}"
                )
        for table in card.tables:
            assert table.description, f"{card.template}/{table.name}: no description"
