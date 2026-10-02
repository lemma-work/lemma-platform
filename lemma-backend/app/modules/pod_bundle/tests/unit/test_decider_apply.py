"""Deciders in a bundle: how the plan reads them, how a step saves one, and who
may export them -- against an in-memory stand-in for the decisions contract."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.core.authorization.context import ResourceRef, ResourceType
from app.core.domain.errors import DomainError
from app.modules.decisions.contracts.deciders import DeciderEntity
from app.modules.decisions.contracts.shapes import DeciderDefinition
from app.modules.pod_bundle.domain.errors import BundleInvalidError
from app.modules.pod_bundle.domain.state import (
    PlanStep,
    StepAction,
    StepKind,
    StepStatus,
)
from app.modules.pod_bundle.infrastructure.applier import BundleApplier
from app.modules.pod_bundle.infrastructure.decider_apply import (
    DeciderSnapshot,
    plan_deciders,
)
from app.modules.pod_bundle.infrastructure.exporter_deciders import export_deciders

_DECIDERS = "app.modules.decisions.contracts.deciders"
_POD = uuid4()
_IMPORTER = uuid4()

TRIAGE = {
    "description": "What Kit does with each new email.",
    "input": {"fields": ["from", "subject", "labels"]},
    "questions": {
        "action": {
            "type": "choice",
            "prompt": "What should Kit do with this email?",
            "options": {"act": "A customer is waiting.", "ignore": "Newsletters."},
            "fallback": "ignore",
        }
    },
    "rules": [
        {"when": "contains(labels, 'PROMOTIONS')", "answer": {"action": "ignore"}}
    ],
}
REVISED = {**TRIAGE, "guidance": "Invoices are Kit's to file."}


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _bundle(tmp_path: Path, **deciders: dict[str, object]) -> Path:
    root = tmp_path / "bundle"
    _write(root / "pod.json", {"name": "Inbox", "format_version": 3})
    for name, definition in deciders.items():
        _write(
            root / "deciders" / name / f"{name}.json",
            {"name": name, "definition": definition},
        )
    return root


def _decider(name: str, definition: dict[str, object], version: int) -> DeciderEntity:
    return DeciderEntity(
        pod_id=_POD,
        name=name,
        version=version,
        definition=DeciderDefinition.model_validate(definition),
    )


class FakeDeciders:
    """The decider operations an import names, over one pod's deciders."""

    def __init__(self, *deciders: DeciderEntity) -> None:
        self.by_name = {decider.name: decider for decider in deciders}

    async def get_decider(self, *, pod_id: UUID, name: str) -> DeciderEntity | None:
        return self.by_name.get(name)

    async def list_deciders(
        self, *, pod_id: UUID, limit: int = 100
    ) -> list[DeciderEntity]:
        return sorted(self.by_name.values(), key=lambda decider: decider.name)[:limit]

    async def define_decider(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        name: str,
        definition: DeciderDefinition,
    ) -> tuple[DeciderEntity, list[str]]:
        current = self.by_name.get(name)
        saved = DeciderEntity(
            pod_id=pod_id,
            user_id=user_id,
            name=name,
            version=1 if current is None else current.version + 1,
            definition=definition,
        )
        self.by_name[name] = saved
        return saved, []

    def patch(self, monkeypatch: pytest.MonkeyPatch) -> FakeDeciders:
        for name in ("get_decider", "list_deciders", "define_decider"):
            monkeypatch.setattr(f"{_DECIDERS}.{name}", getattr(self, name))
        return self


class FakeContext:
    """Allows everything but what it is told to refuse, and keeps what it was
    asked, so a test can read which permission a step needed."""

    def __init__(self, *, refuse: frozenset[str] = frozenset()) -> None:
        self.refuse = refuse
        self.asked: list[tuple[str, ResourceRef | None]] = []

    async def require(self, permission_id: str, resource: ResourceRef | None = None):
        self.asked.append((permission_id, resource))
        if permission_id in self.refuse:
            raise DomainError(
                f"Missing permission {permission_id}",
                code="PERMISSION_DENIED",
                status_code=403,
            )

    async def can(self, permission_id: str, resource: ResourceRef | None = None):
        self.asked.append((permission_id, resource))
        return permission_id not in self.refuse


class FakeExisting:
    def __init__(self, *deciders: DeciderEntity) -> None:
        self._deciders = {
            decider.name: DeciderSnapshot(
                id=decider.id, version=decider.version, definition=decider.definition
            )
            for decider in deciders
        }
        self.asked = 0

    async def deciders(self) -> dict[str, DeciderSnapshot]:
        self.asked += 1
        return self._deciders


def _step(name: str) -> PlanStep:
    return PlanStep(index=0, kind=StepKind.DECIDER, name=name, action=StepAction.CREATE)


async def _apply(root: Path, ctx: FakeContext, name: str, **replacements: str) -> None:
    applier = BundleApplier(
        uow=object(),
        ctx=ctx,
        pod_id=_POD,
        user_id=_IMPORTER,
        bundle_root=root,
        replacements=replacements,
    )
    await applier.apply_step(_step(name))


# --- apply -------------------------------------------------------------------


async def test_a_new_decider_is_created_by_the_person_importing_it(
    tmp_path, monkeypatch
):
    deciders = FakeDeciders().patch(monkeypatch)
    ctx = FakeContext()

    await _apply(_bundle(tmp_path, triage=TRIAGE), ctx, "triage")

    saved = deciders.by_name["triage"]
    assert (saved.pod_id, saved.user_id, saved.version) == (_POD, _IMPORTER, 1)
    assert saved.definition == DeciderDefinition.model_validate(TRIAGE)
    assert ctx.asked == [("decider.create", ResourceRef.pod(_POD))]


async def test_a_changed_decider_saves_its_next_version(tmp_path, monkeypatch):
    existing = _decider("triage", TRIAGE, version=2)
    deciders = FakeDeciders(existing).patch(monkeypatch)
    ctx = FakeContext()

    await _apply(_bundle(tmp_path, triage=REVISED), ctx, "triage")

    saved = deciders.by_name["triage"]
    assert saved.version == 3
    assert saved.definition.guidance == "Invoices are Kit's to file."
    assert ctx.asked == [
        (
            "decider.update",
            ResourceRef(
                resource_type=ResourceType.DECIDER, resource_id=existing.id, pod_id=_POD
            ),
        )
    ]


async def test_an_unchanged_decider_saves_no_version(tmp_path, monkeypatch):
    """Importing the same bundle twice, or replaying a step after a crash,
    must leave the decider's history alone."""
    existing = _decider("triage", TRIAGE, version=2)
    deciders = FakeDeciders(existing).patch(monkeypatch)
    ctx = FakeContext()

    await _apply(_bundle(tmp_path, triage=TRIAGE), ctx, "triage")

    assert deciders.by_name["triage"] is existing
    assert ctx.asked == []


async def test_someone_who_may_not_create_deciders_imports_none(tmp_path, monkeypatch):
    deciders = FakeDeciders().patch(monkeypatch)
    ctx = FakeContext(refuse=frozenset({"decider.create"}))

    with pytest.raises(DomainError) as refused:
        await _apply(_bundle(tmp_path, triage=TRIAGE), ctx, "triage")

    assert refused.value.status_code == 403
    assert deciders.by_name == {}


async def test_a_variable_in_a_definition_is_resolved_before_it_is_saved(
    tmp_path, monkeypatch
):
    deciders = FakeDeciders().patch(monkeypatch)
    definition = {**TRIAGE, "guidance": "Escalate to ${team}."}

    await _apply(
        _bundle(tmp_path, triage=definition), FakeContext(), "triage", team="Support"
    )

    assert deciders.by_name["triage"].definition.guidance == "Escalate to Support."


# --- plan --------------------------------------------------------------------


async def test_the_plan_creates_updates_or_skips_each_decider(tmp_path):
    root = _bundle(tmp_path, fresh=TRIAGE, revised=REVISED, same=TRIAGE)
    existing = FakeExisting(
        _decider("revised", TRIAGE, version=4), _decider("same", TRIAGE, version=2)
    )

    steps = await plan_deciders(root, existing)

    assert [
        (step.name, step.action, step.status, step.detail["version"]) for step in steps
    ] == [
        ("fresh", StepAction.CREATE, StepStatus.PENDING, 1),
        ("revised", StepAction.UPDATE, StepStatus.PENDING, 5),
        ("same", StepAction.SKIP, StepStatus.SKIPPED, 2),
    ]
    assert {step.kind for step in steps} == {StepKind.DECIDER}


async def test_a_bundle_without_deciders_does_not_ask_the_pod_for_its_own(
    tmp_path,
):
    existing = FakeExisting()

    assert await plan_deciders(_bundle(tmp_path), existing) == []
    assert existing.asked == 0


async def test_a_definition_no_pod_could_save_refuses_the_plan(tmp_path):
    broken = {**TRIAGE, "rules": [{"when": "true", "answer": {"tone": "warm"}}]}

    with pytest.raises(BundleInvalidError) as refused:
        await plan_deciders(_bundle(tmp_path, triage=broken), FakeExisting())

    assert "Decider 'triage'" in refused.value.message
    assert "unknown question 'tone'" in refused.value.message


async def test_a_name_no_pod_accepts_refuses_the_plan(tmp_path):
    with pytest.raises(BundleInvalidError) as refused:
        await plan_deciders(_bundle(tmp_path, Triage=TRIAGE), FakeExisting())

    assert "Decider 'Triage'" in refused.value.message


async def test_a_decider_file_left_loose_is_refused_not_skipped(tmp_path):
    """Every reader of a bundle looks in deciders/<name>/, so a loose file
    would otherwise be carried into the pod by nothing, silently."""
    root = _bundle(tmp_path)
    _write(root / "deciders" / "triage.json", {"name": "triage", "definition": TRIAGE})

    with pytest.raises(BundleInvalidError) as refused:
        await plan_deciders(root, FakeExisting())

    assert "deciders/triage.json" in refused.value.message


# --- export ------------------------------------------------------------------


async def test_someone_who_may_not_read_deciders_exports_none_and_is_told(
    tmp_path, monkeypatch
):
    FakeDeciders(_decider("triage", TRIAGE, version=1)).patch(monkeypatch)
    warnings: list[str] = []

    await export_deciders(
        tmp_path,
        pod_id=_POD,
        ctx=FakeContext(refuse=frozenset({"decider.read"})),
        warnings=warnings,
    )

    assert not (tmp_path / "deciders").exists()
    assert warnings == ["deciders were left out: you may not read this pod's deciders."]
