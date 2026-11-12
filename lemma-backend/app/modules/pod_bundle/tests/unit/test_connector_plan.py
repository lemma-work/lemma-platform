"""Planning a bundle's pod connectors, and checking the importer's choices.

The plan builder runs over a fake :class:`ConnectorLookup` -- the importing
pod's declared connectors, the org's installations, the importer's accounts
and authority -- so what is tested is the plan's own decisions: which
installation is the default, what blocks, what only warns, and that no account
is ever shared by default.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.modules.pod_bundle.domain.connector_plan import (
    AccountOption,
    ConnectorChoice,
    ConnectorPlanStatus,
    DeclaredConnector,
    InstallationOption,
    resolve_connector_choices,
)
from app.modules.pod_bundle.domain.errors import BundleInvalidError
from app.modules.pod_bundle.domain.state import StepKind, StepStatus
from app.modules.pod_bundle.infrastructure.plan_builder import PlanBuilder


class _Lookup:
    def __init__(
        self,
        *,
        declared: dict[str, DeclaredConnector] | None = None,
        installations: dict[str, list[InstallationOption]] | None = None,
        accounts: list[AccountOption] | None = None,
        may_install: bool = False,
        installable: set[str] | None = None,
    ):
        self._declared = declared or {}
        self._installations = installations or {}
        self._accounts = accounts or []
        self._may_install = may_install
        self._installable = installable or set()
        self.asked_accounts_for: list[list[UUID]] = []

    async def declared(self):
        return dict(self._declared)

    async def installations(self, connector_ids):
        return {
            c: self._installations[c] for c in connector_ids if c in self._installations
        }

    async def own_accounts(self, auth_config_ids):
        self.asked_accounts_for.append(list(auth_config_ids))
        return [a for a in self._accounts if a.auth_config_id in set(auth_config_ids)]

    async def may_install(self):
        return self._may_install

    async def installable(self, connector_id):
        return connector_id in self._installable


class _NoResources:
    async def table_names(self):
        return set()

    async def table_manifest(self, name):
        return None

    async def function_names(self):
        return set()

    async def agent_names(self):
        return set()

    async def workflow_names(self):
        return set()

    async def schedule_names(self):
        return set()

    async def app_names(self):
        return set()

    async def surface_names(self):
        return set()

    async def machine_names(self):
        return set()


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / "bundle"
    _write(root / "pod.json", {"name": "support", "format_version": 3})
    _write(
        root / "connectors" / "support-inbox" / "support-inbox.json",
        {"connector": "gmail", "mode": "SHARED", "purpose": "Team mailbox"},
    )
    _write(
        root / "connectors" / "crm" / "crm.json",
        {"connector": "hubspot", "mode": "PER_MEMBER", "required": True},
    )
    _write(
        root / "schedules" / "on_mail" / "on_mail.json",
        {
            "name": "on_mail",
            "schedule_type": "WEBHOOK",
            "pod_connector": "support-inbox",
        },
    )
    _write(
        root / "agents" / "triage" / "triage.json",
        {
            "name": "triage",
            "instruction": "Triage.",
            "permissions": {
                "grants": [
                    {
                        "resource_type": "connector",
                        "resource_name": "support-inbox",
                        "permission_ids": ["connector.use"],
                    }
                ]
            },
        },
    )
    return root


def _install(name: str, *, default: bool = False) -> InstallationOption:
    return InstallationOption(auth_config_id=uuid4(), name=name, is_default=default)


async def _plan(root: Path, lookup: _Lookup):
    return await PlanBuilder(_NoResources(), connectors=lookup).build_plan(
        bundle_root=root
    )


async def test_connectors_are_planned_first_with_the_orgs_default_installation(
    tmp_path,
):
    gmail_default, gmail_other = _install("gmail", default=True), _install("gmail-2")
    hubspot = _install("hubspot", default=True)
    plan = await _plan(
        _bundle(tmp_path),
        _Lookup(
            installations={"gmail": [gmail_default, gmail_other], "hubspot": [hubspot]}
        ),
    )

    kinds = [step.kind for step in plan.steps]
    assert kinds[:2] == [StepKind.CONNECTOR, StepKind.CONNECTOR]
    assert StepKind.CONNECTOR not in kinds[2:]
    by_slug = {c.slug: c for c in plan.connectors}
    inbox = by_slug["support-inbox"]
    # Several installations: the plan shows the choice, the default chosen.
    assert [o.name for o in inbox.installations] == ["gmail", "gmail-2"]
    assert inbox.auth_config_id == gmail_default.auth_config_id
    assert inbox.used_by == ["schedules/on_mail", "agents/triage"]
    assert by_slug["crm"].status is ConnectorPlanStatus.READY
    assert any("members will be asked to connect" in w for w in plan.warnings)


async def test_a_shared_connector_offers_only_the_importers_own_accounts_and_shares_none(
    tmp_path,
):
    gmail = _install("gmail", default=True)
    mine = AccountOption(
        account_id=uuid4(), auth_config_id=gmail.auth_config_id, label="me@x"
    )
    plan = await _plan(
        _bundle(tmp_path),
        _Lookup(
            installations={"gmail": [gmail], "hubspot": [_install("hubspot")]},
            accounts=[mine],
        ),
    )

    inbox = next(c for c in plan.connectors if c.slug == "support-inbox")
    assert inbox.status is ConnectorPlanStatus.NEEDS_ACCOUNT
    assert inbox.account_options == [mine]
    assert any("imports needing an account" in w for w in plan.warnings)
    # Taking the defaults shares nothing: a credential is shared on purpose.
    resolved, blocking = resolve_connector_choices(plan.connectors, {})
    assert blocking == []
    assert resolved["support-inbox"].share_account_id is None
    assert resolved["support-inbox"].auth_config_id == gmail.auth_config_id


async def test_a_connector_with_no_installation_blocks_unless_one_may_be_created(
    tmp_path,
):
    lookup = _Lookup(installations={"gmail": [_install("gmail")]})
    plan = await _plan(_bundle(tmp_path), lookup)

    crm = next(c for c in plan.connectors if c.slug == "crm")
    assert crm.status is ConnectorPlanStatus.NEEDS_INSTALLATION
    assert crm.can_create_installation is False
    assert "Ask an admin" in (crm.message or "")
    _, blocking = resolve_connector_choices(plan.connectors, {})
    assert [b.slug for b in blocking] == ["crm"]
    _, refused = resolve_connector_choices(
        plan.connectors, {"crm": ConnectorChoice(create_installation=True)}
    )
    assert [b.slug for b in refused] == ["crm"]


async def test_an_installer_may_choose_to_create_the_missing_installation(tmp_path):
    plan = await _plan(
        _bundle(tmp_path),
        _Lookup(
            installations={"gmail": [_install("gmail")]},
            may_install=True,
            installable={"hubspot"},
        ),
    )

    crm = next(c for c in plan.connectors if c.slug == "crm")
    assert crm.status is ConnectorPlanStatus.NEEDS_INSTALLATION
    assert crm.can_create_installation is True
    resolved, blocking = resolve_connector_choices(
        plan.connectors, {"crm": ConnectorChoice(create_installation=True)}
    )
    assert blocking == []
    assert resolved["crm"].create_installation is True


async def test_a_slug_the_pod_already_declares_is_kept_or_conflicts(tmp_path):
    plan = await _plan(
        _bundle(tmp_path),
        _Lookup(
            declared={
                "support-inbox": DeclaredConnector(connector="gmail", mode="SHARED"),
                "crm": DeclaredConnector(connector="salesforce", mode="PER_MEMBER"),
            },
            installations={"gmail": [_install("gmail")], "hubspot": [_install("h")]},
        ),
    )

    by_slug = {c.slug: c for c in plan.connectors}
    assert by_slug["support-inbox"].status is ConnectorPlanStatus.EXISTS
    assert by_slug["crm"].status is ConnectorPlanStatus.CONFLICT
    steps = {s.name: s for s in plan.steps if s.kind is StepKind.CONNECTOR}
    # Still run on apply, which checks the pod's declaration again.
    assert steps["support-inbox"].status is StepStatus.PENDING
    assert steps["crm"].status is StepStatus.SKIPPED
    _, blocking = resolve_connector_choices(plan.connectors, {})
    assert [b.slug for b in blocking] == ["crm"]


async def test_a_slug_the_pod_declares_in_the_other_mode_is_a_conflict(tmp_path):
    """Same catalog connector, other mode: keeping the pod's would run the
    bundle's resources as one shared mailbox where each person's own was
    meant, or the reverse -- so it blocks, typed, instead of being kept."""
    plan = await _plan(
        _bundle(tmp_path),
        _Lookup(
            declared={
                "support-inbox": DeclaredConnector(
                    connector="gmail", mode="PER_MEMBER"
                ),
            },
            installations={"gmail": [_install("gmail")], "hubspot": [_install("h")]},
        ),
    )

    inbox = next(c for c in plan.connectors if c.slug == "support-inbox")
    assert inbox.status is ConnectorPlanStatus.CONFLICT
    assert "per member" in (inbox.message or "")
    _, blocking = resolve_connector_choices(plan.connectors, {})
    assert [b.slug for b in blocking] == ["support-inbox"]


async def test_a_bundle_of_a_newer_format_is_refused_up_front(tmp_path):
    from lemma_pod_bundle import FORMAT_VERSION

    root = _bundle(tmp_path)
    _write(root / "pod.json", {"name": "x", "format_version": FORMAT_VERSION + 1})

    with pytest.raises(BundleInvalidError, match="Update the Lemma CLI"):
        await _plan(root, _Lookup())


@pytest.mark.parametrize(
    "choice",
    [
        ConnectorChoice(auth_config_id=uuid4()),
        ConnectorChoice(share_account_id=uuid4()),
    ],
)
async def test_a_choice_outside_what_the_plan_offered_is_refused(tmp_path, choice):
    plan = await _plan(
        _bundle(tmp_path),
        _Lookup(
            installations={"gmail": [_install("gmail")], "hubspot": [_install("h")]}
        ),
    )

    _, blocking = resolve_connector_choices(plan.connectors, {"support-inbox": choice})
    assert [b.slug for b in blocking] == ["support-inbox"]


async def test_a_choice_for_an_undeclared_slug_is_refused(tmp_path):
    plan = await _plan(
        _bundle(tmp_path),
        _Lookup(
            installations={"gmail": [_install("gmail")], "hubspot": [_install("h")]}
        ),
    )
    _, blocking = resolve_connector_choices(
        plan.connectors, {"typo": ConnectorChoice()}
    )
    assert [b.slug for b in blocking] == ["typo"]


async def test_a_resource_naming_an_undeclared_slug_is_warned_about(tmp_path):
    root = tmp_path / "bundle"
    _write(root / "pod.json", {"name": "x", "format_version": 3})
    _write(
        root / "surfaces" / "slack" / "slack.json",
        {"platform": "SLACK", "pod_connector": "slack-bot"},
    )

    plan = await _plan(root, _Lookup())

    assert plan.connectors == []
    assert any("slack-bot" in w and "neither" in w for w in plan.warnings)


async def test_a_malformed_connector_fails_planning(tmp_path):
    root = tmp_path / "bundle"
    _write(root / "pod.json", {"name": "x", "format_version": 3})
    _write(
        root / "connectors" / "gmail" / "gmail.json",
        {"connector": "gmail", "mode": "SHARED", "auth_config_id": str(uuid4())},
    )

    with pytest.raises(BundleInvalidError, match="auth_config_id"):
        await _plan(root, _Lookup())


@pytest.mark.parametrize(
    ("declared", "refused"),
    [
        (DeclaredConnector(connector="hubspot", mode="PER_MEMBER"), False),
        (DeclaredConnector(connector="hubspot", mode="SHARED"), True),
        (DeclaredConnector(connector="salesforce", mode="PER_MEMBER"), True),
    ],
)
async def test_apply_is_as_strict_as_the_plan_about_a_slug_the_pod_has(
    tmp_path, monkeypatch, declared, refused
):
    """Declared since the plan, or replayed: the step keeps the pod's own only
    when the plan would have, and refuses it, typed, for the plan's reason."""
    from app.modules.pod_bundle.domain.errors import PodBundleDomainError
    from app.modules.pod_bundle.domain.state import PlanStep, StepAction
    from app.modules.pod_bundle.infrastructure.connector_apply import apply_connector

    async def _declared(uow, *, pod_id):
        del uow, pod_id
        return {"crm": declared}

    monkeypatch.setattr(
        "app.modules.pod_bundle.infrastructure.connector_planning.declared_in_pod",
        _declared,
    )
    step = PlanStep(
        index=0, kind=StepKind.CONNECTOR, name="crm", action=StepAction.CREATE
    )
    run = apply_connector(
        step,
        uow=object(),
        ctx=object(),
        pod_id=uuid4(),
        user_id=uuid4(),
        bundle_root=_bundle(tmp_path),
        choices={},
        warnings=[],
    )
    if not refused:
        assert await run is None
        return
    with pytest.raises(PodBundleDomainError) as raised:
        await run
    assert raised.value.code == "POD_BUNDLE_CONNECTOR_CONFLICT"
