"""Bundled grants on a connector the importing pod does not declare.

Settled at planning, so an import refuses before writing anything rather than
failing at the grants step after its agents exist; declared on apply through
the one helper that declares pod connectors everywhere.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.core.authorization.context import ResourceType
from app.modules.connectors.contracts.bundles import WaitingConnectorGrant
from app.modules.pod_bundle.domain.connector_plan import ConnectorChoice
from app.modules.pod_bundle.domain.errors import BundleInvalidError
from app.modules.pod_bundle.domain.state import StepKind
from app.modules.pod_bundle.infrastructure.connector_grants import (
    plan_connector_grants,
    resolve_connector_grants,
)
from app.modules.pod_bundle.infrastructure.grants import GrantInput
from app.modules.pod_bundle.infrastructure.plan_builder import PlanBuilder

_POD = uuid4()
_ORG = uuid4()
_GITHUB_INSTALL = uuid4()


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _grant(name: str) -> dict:
    return {
        "resource_type": "connector",
        "resource_name": name,
        "permission_ids": ["connector.use"],
    }


def _bundle(tmp_path: Path, *connectors: str) -> Path:
    root = tmp_path / "bundle"
    _write(root / "pod.json", {"name": "Ops", "format_version": 2})
    _write(
        root / "agents" / "triage" / "triage.json",
        {
            "name": "triage",
            "permissions": {"grants": [_grant(name) for name in connectors]},
        },
    )
    return root


class Directory:
    """The importing pod's connectors and its organization's installations."""

    def __init__(self, *, declared=(), installed=None):
        self._declared = set(declared)
        self._installed = dict(installed or {})

    async def declares(self, slug: str) -> bool:
        return slug in self._declared

    async def installation_for(self, name: str) -> UUID | None:
        return self._installed.get(name)


async def test_a_connector_the_pod_lacks_but_the_org_installed_is_declared(
    tmp_path,
):
    warnings = await plan_connector_grants(
        _bundle(tmp_path, "github"),
        Directory(installed={"github": _GITHUB_INSTALL}),
    )

    [warning] = warnings
    assert "'github'" in warning and "agent 'triage'" in warning


async def test_only_the_grants_the_import_writes_are_planned(tmp_path):
    """An agent the pod already has keeps its own access, so what its bundled
    copy was granted is neither declared nor a reason to refuse the import."""
    warnings = await plan_connector_grants(
        _bundle(tmp_path, "github", "jira"), Directory(), writing=set()
    )

    assert warnings == []


async def test_a_declared_connector_needs_nothing(tmp_path):
    warnings = await plan_connector_grants(
        _bundle(tmp_path, "github"), Directory(declared={"github"})
    )

    assert warnings == []


async def test_a_connector_nobody_installed_refuses_the_whole_plan(tmp_path):
    """Refused while planning, naming the connector and who holds it -- not
    at the grants step, after the agent it grants to was written."""
    with pytest.raises(BundleInvalidError) as refused:
        await plan_connector_grants(
            _bundle(tmp_path, "github", "jira"),
            Directory(installed={"github": _GITHUB_INSTALL}),
        )

    message = str(refused.value)
    assert "'jira'" in message and "agent 'triage'" in message
    assert "'github'" not in message


class _NothingExists:
    """A pod with no resources at all, for the plan builder."""

    async def _none(self, *_args):
        return set()

    table_names = function_names = agent_names = _none
    workflow_names = schedule_names = app_names = surface_names = machine_names = _none

    async def table_manifest(self, _name):
        return None


async def test_the_plan_carries_the_declaration_as_a_warning(tmp_path):
    builder = PlanBuilder(
        _NothingExists(), Directory(installed={"github": _GITHUB_INSTALL})
    )

    plan = await builder.build_plan(bundle_root=_bundle(tmp_path, "github"))

    assert any(s.kind is StepKind.AGENT_GRANTS for s in plan.steps)
    assert any("'github'" in w for w in plan.warnings)


async def test_the_plan_builder_refuses_before_any_step(tmp_path):
    builder = PlanBuilder(_NothingExists(), Directory())

    with pytest.raises(BundleInvalidError):
        await builder.build_plan(bundle_root=_bundle(tmp_path, "jira"))


def _connector_grant(name: str) -> GrantInput:
    return GrantInput(
        resource_type=ResourceType.CONNECTOR,
        resource_name=name,
        permission_ids=["connector.use"],
    )


async def test_applying_declares_the_connector_and_grants_on_its_slug(monkeypatch):
    declared: list[tuple[UUID, UUID]] = []

    async def get_pod_connector(_uow, *, pod_id, slug):
        return SimpleNamespace(slug=slug) if slug == "gmail" else None

    async def installation_for_bundle_name(_uow, *, organization_id, name):
        assert organization_id == _ORG
        return _GITHUB_INSTALL if name == "github" else None

    async def ensure(_uow, _ctx, *, pod_id, auth_config_id):
        declared.append((pod_id, auth_config_id))
        return SimpleNamespace(slug="github-2")

    monkeypatch.setattr(
        "app.modules.connectors.contracts.pod_connectors.get_pod_connector",
        get_pod_connector,
    )
    monkeypatch.setattr(
        "app.modules.connectors.contracts.provisioning.installation_for_bundle_name",
        installation_for_bundle_name,
    )
    monkeypatch.setattr(
        "app.modules.connectors.contracts.pod_connectors."
        "ensure_pod_connector_for_installation",
        ensure,
        raising=False,
    )
    table = GrantInput(
        resource_type=ResourceType.DATASTORE_TABLE,
        resource_name="tickets",
        permission_ids=["datastore.record.read"],
    )
    grants = [
        table,
        _connector_grant("gmail"),
        _connector_grant("github"),
        _connector_grant("github"),
    ]

    resolved = await resolve_connector_grants(
        object(),
        SimpleNamespace(organization_id=_ORG),
        pod_id=_POD,
        grants=grants,
    )

    # Declared once for the installation, and every grant on it renamed.
    assert declared == [(_POD, _GITHUB_INSTALL)]
    assert [g.resource_name for g in resolved] == [
        "tickets",
        "gmail",
        "github-2",
        "github-2",
    ]

    # A connector the import left out (optional, nothing to map it to): the
    # grant on it is dropped and said, not written to fail by name.
    warnings: list[str] = []
    kept = await resolve_connector_grants(
        object(),
        SimpleNamespace(organization_id=_ORG),
        pod_id=_POD,
        grants=[table, _connector_grant("meet"), _connector_grant("gmail")],
        choices={"meet": ConnectorChoice(skip=True)},
        warnings=warnings,
    )
    assert [g.resource_name for g in kept] == ["tickets", "gmail"]
    assert warnings == ["A grant on connector 'meet' was dropped: it was left out."]


async def test_grants_with_no_connector_are_returned_untouched():
    table = GrantInput(
        resource_type=ResourceType.DATASTORE_TABLE,
        resource_name="tickets",
        permission_ids=["datastore.record.read"],
    )

    resolved = await resolve_connector_grants(
        object(), SimpleNamespace(organization_id=_ORG), pod_id=_POD, grants=[table]
    )

    assert resolved == [table]


async def test_a_left_out_grant_waits_only_while_the_org_has_nothing_to_map_it_to(
    monkeypatch, tmp_path
):
    """Left out because the organization has no installation of it: the grant
    waits for the pod to declare one. Left out while it has one: that was the
    importer's choice, and the grant is dropped as they were told."""
    installed: dict[str, UUID] = {}

    async def get_pod_connector(_uow, *, pod_id, slug):
        return None

    async def installation_for_bundle_name(_uow, *, organization_id, name):
        return installed.get(name)

    monkeypatch.setattr(
        "app.modules.connectors.contracts.pod_connectors.get_pod_connector",
        get_pod_connector,
    )
    monkeypatch.setattr(
        "app.modules.connectors.contracts.provisioning.installation_for_bundle_name",
        installation_for_bundle_name,
    )
    root = tmp_path / "bundle"
    _write(
        root / "connectors" / "calendar" / "calendar.json",
        {"connector": "google_calendar", "mode": "PER_MEMBER", "required": False},
    )

    async def resolve() -> tuple[
        list[GrantInput], list[WaitingConnectorGrant], list[str]
    ]:
        waiting: list[WaitingConnectorGrant] = []
        warnings: list[str] = []
        kept = await resolve_connector_grants(
            object(),
            SimpleNamespace(organization_id=_ORG),
            pod_id=_POD,
            grants=[_connector_grant("calendar")],
            choices={"calendar": ConnectorChoice(skip=True)},
            warnings=warnings,
            bundle_root=root,
            waiting=waiting,
        )
        return kept, waiting, warnings

    kept, waiting, warnings = await resolve()
    assert kept == []
    assert [(w.connector, w.mode, w.permission_ids) for w in waiting] == [
        ("google_calendar", "PER_MEMBER", ("connector.use",))
    ]
    waits = (
        "A grant on connector 'calendar' waits for it: it was left out, and is "
        "granted once this pod has a google_calendar connector the whole pod "
        "can use, with each member using their own account."
    )
    assert warnings == [waits]

    installed["google_calendar"] = uuid4()
    kept, waiting, warnings = await resolve()
    assert (kept, waiting) == ([], [])
    assert warnings == ["A grant on connector 'calendar' was dropped: it was left out."]


async def test_the_importer_is_told_a_grant_was_dropped_when_none_can_wait():
    """Before migration 0078 nothing can be held: the warning resolving wrote
    ("waits for it") is replaced by the one that is true ("was dropped")."""
    from app.modules.pod_bundle.infrastructure.connector_grants import (
        hold_waiting_grants,
    )

    async def nothing_held(*_args: object, **_kwargs: object) -> bool:
        return False

    promised = (
        "A grant on connector 'calendar' waits for it: it was left out, and is "
        "granted once this pod has a google_calendar connector the whole pod "
        "can use, with each member using their own account."
    )
    warnings = [promised, "Something else."]

    await hold_waiting_grants(
        SimpleNamespace(session=None),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        pod_id=_POD,
        grantee_type="FUNCTION",
        grantee_id=uuid4(),
        waiting=[
            WaitingConnectorGrant(
                connector="google_calendar",
                mode="PER_MEMBER",
                permission_ids=("connector.use",),
                slug="calendar",
            )
        ],
        created_by_user_id=None,
        warnings=warnings,
        hold=nothing_held,
    )

    assert warnings == [
        "Something else.",
        "A grant on connector 'calendar' was dropped: it was left out.",
    ]


async def test_the_grant_step_keeps_its_locks_until_it_commits(monkeypatch):
    """An authorization check releases a clean session by committing it,
    which would let the waiting-grant locks go before the grants are
    replaced. The step marks its session so that cannot happen."""
    from app.core.infrastructure.db.transaction_locks import (
        holds_transaction_scoped_lock,
        safe_to_release,
    )
    from app.modules.pod_bundle.infrastructure.connector_grants import (
        lock_waiting_grants,
    )

    async def locked(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        "app.core.authorization.pending_grants.lock_grantee_pending_keys", locked
    )
    session = SimpleNamespace(info={}, new=(), dirty=(), deleted=())

    await lock_waiting_grants(
        SimpleNamespace(session=session),  # type: ignore[arg-type]
        pod_id=_POD,
        grantee_type="FUNCTION",
        grantee_id=uuid4(),
    )

    assert holds_transaction_scoped_lock(session)
    assert not safe_to_release(session)
