"""A plugin install's rules: the page's defaults, where each bundle is, the note,
and what starting and retrying an install write (faked store and queue)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.modules.pod_bundle.api.install_schemas import (
    PluginInstallRequest,
    PluginInstallResponse,
)
from app.modules.pod_bundle.application.install_use_cases import (
    Install,
    InstallUseCases,
    PluginSource,
    install_delay,
)
from app.modules.pod_bundle.domain.connector_plan import (
    ConnectorChoice,
    ConnectorPlan,
    ConnectorPlanStatus,
)
from app.modules.pod_bundle.domain.errors import BundleJobNotFoundError
from app.modules.pod_bundle.domain.plugin_install import (
    PluginInstallState,
    PageDefaults,
    outcome_of,
    page_defaults,
    settled,
    settled_note,
    slug_of,
)
from app.modules.pod_bundle.domain.state import (
    BundleSource,
    BundleSourceKind,
    ImportPlan,
    ImportState,
    ImportStatus,
    InstallSlot,
    PlanStep,
    StepAction,
    StepKind,
    StepStatus,
    VariableSpec,
)
from app.modules.pod_bundle.events.work import INSTALL_PLUGIN, plan_key


def _plan(**kw) -> ImportPlan:
    return ImportPlan(format_version=4, bundle_name="Mail", **kw)


def _connector(status: ConnectorPlanStatus, **kw) -> ConnectorPlan:
    return ConnectorPlan(
        slug=kw.pop("slug", "gmail"),
        connector="gmail",
        mode="PER_MEMBER",
        status=status,
        **kw,
    )


# --- the page's defaults ------------------------------------------------------


def test_a_required_name_is_the_lemmas_name_as_a_slug():
    plan = _plan(
        variables=[
            VariableSpec(name="app_slug", kind="free", required=True),
            VariableSpec(name="owner", kind="pod_member"),
            VariableSpec(name="channel", kind="free", default="general"),
        ]
    )
    defaults = page_defaults(plan, "Northwind Sales!")
    assert defaults == PageDefaults(
        variables={"app_slug": "northwind-sales", "owner": "", "channel": "general"},
        connectors={},
    )


def test_an_optional_name_with_no_default_is_left_empty():
    """Only a deprecated account variable is one: its own words ask to leave
    it empty, and the Lemma's name is no account id."""
    plan = _plan(variables=[VariableSpec(name="on_ticket_account", kind="free")])
    defaults = page_defaults(plan, "Northwind")
    assert isinstance(defaults, PageDefaults)
    assert defaults.variables == {"on_ticket_account": ""}


def test_a_required_name_it_cannot_derive_is_a_question():
    plan = _plan(variables=[VariableSpec(name="app_slug", kind="free", required=True)])
    assert page_defaults(plan, "!!!") == "It needs a value for app_slug."


def test_a_plan_that_would_change_something_is_a_question():
    step = PlanStep(
        index=0,
        kind=StepKind.TABLE,
        name="t",
        action=StepAction.UPDATE,
        destructive=True,
    )
    assert (
        page_defaults(_plan(steps=[step]), "Northwind")
        == "It would change something already in this Lemma."
    )


@pytest.mark.parametrize(
    "status", [ConnectorPlanStatus.NEEDS_INSTALLATION, ConnectorPlanStatus.SKIPPED]
)
def test_a_connector_with_no_installation_gets_lemmas_app_where_it_may(status):
    plan = _plan(connectors=[_connector(status, can_create_installation=True)])
    defaults = page_defaults(plan, "Northwind")
    assert isinstance(defaults, PageDefaults)
    assert defaults.connectors == {"gmail": ConnectorChoice(create_installation=True)}


def test_a_required_connector_nobody_may_install_is_a_question():
    plan = _plan(
        connectors=[
            _connector(
                ConnectorPlanStatus.NEEDS_INSTALLATION,
                message="Your organization has not installed gmail.",
            )
        ]
    )
    assert page_defaults(plan, "Northwind") == (
        "Your organization has not installed gmail."
    )


def test_an_optional_connector_nobody_may_install_is_left_out():
    plan = _plan(connectors=[_connector(ConnectorPlanStatus.SKIPPED)])
    assert page_defaults(plan, "Northwind") == PageDefaults(variables={}, connectors={})


def test_a_connector_whose_name_is_taken_is_a_question():
    plan = _plan(
        connectors=[
            _connector(ConnectorPlanStatus.CONFLICT, can_create_installation=True)
        ]
    )
    assert page_defaults(plan, "Northwind") == "Its name is taken in this Lemma."


@pytest.mark.parametrize(
    ("status", "kw"),
    [
        (ConnectorPlanStatus.EXISTS, {}),
        (ConnectorPlanStatus.READY, {"auth_config_id": uuid4()}),
    ],
)
def test_a_connector_the_page_would_not_ask_about_takes_the_plans_default(status, kw):
    plan = _plan(connectors=[_connector(status, **kw)])
    assert page_defaults(plan, "Northwind") == PageDefaults(variables={}, connectors={})


def test_slug_of():
    assert slug_of("  Smart  Inbox ") == "smart-inbox"


# --- where each bundle is -----------------------------------------------------


def _state(
    status: ImportStatus,
    *,
    position: int = 0,
    asks: str | None = None,
    plan: ImportPlan | None = None,
    error: str | None = None,
    install_id: UUID | None = None,
    user_id: UUID | None = None,
) -> ImportState:
    return ImportState(
        import_id=uuid4(),
        pod_id=uuid4(),
        user_id=user_id or uuid4(),
        status=status,
        source=BundleSource(
            kind=BundleSourceKind.GITHUB,
            repo_url="https://github.com/lemma-work/awesome-lemma",
            path="install/meeting-notes",
        ),
        plan=plan,
        error=error,
        install=InstallSlot(
            install_id=install_id or uuid4(), position=position, asks=asks
        ),
    )


@pytest.mark.parametrize(
    ("status", "asks", "outcome"),
    [
        (ImportStatus.QUEUED, None, PluginInstallState.WAITING),
        (ImportStatus.FETCHING, None, PluginInstallState.INSTALLING),
        (ImportStatus.AWAITING_CONFIRMATION, None, PluginInstallState.INSTALLING),
        (ImportStatus.APPLYING, None, PluginInstallState.INSTALLING),
        (ImportStatus.COMPLETED, None, PluginInstallState.INSTALLED),
        (ImportStatus.FAILED, None, PluginInstallState.FAILED),
        (ImportStatus.CANCELLED, None, PluginInstallState.CANCELLED),
        (
            ImportStatus.CANCELLED,
            "It needs a value for x.",
            PluginInstallState.NEEDS_ANSWER,
        ),
    ],
)
def test_outcome_of(status, asks, outcome):
    assert outcome_of(_state(status, asks=asks)) is outcome


def test_an_install_settles_once_nothing_is_waiting_or_running():
    done = _state(ImportStatus.COMPLETED)
    assert not settled([done, _state(ImportStatus.QUEUED)])
    assert not settled([done, _state(ImportStatus.APPLYING)])
    assert settled([done, _state(ImportStatus.FAILED)])


def test_the_note_says_what_is_ready_and_what_stopped():
    app = PlanStep(index=0, kind=StepKind.APP, name="inbox", action=StepAction.CREATE)
    states = [
        _state(ImportStatus.COMPLETED, plan=_plan(steps=[app])),
        _state(
            ImportStatus.COMPLETED,
            plan=ImportPlan(format_version=4, bundle_name="Meetings"),
        ),
        _state(ImportStatus.CANCELLED, asks="It needs a value for app_slug."),
        _state(ImportStatus.FAILED, error="Step 'inbox' failed: no build."),
    ]
    assert settled_note(states).splitlines() == [
        "Mail and Meetings are ready.",
        (
            "Meeting Notes didn't go in. It needs a value for app_slug. "
            "Finish it on its own page."
        ),
        "Meeting Notes didn't finish. Step 'inbox' failed: no build.",
    ]


def test_the_response_names_the_app_it_put_in_and_the_step_it_is_on():
    app = PlanStep(
        index=1,
        kind=StepKind.APP,
        name="inbox",
        action=StepAction.CREATE,
        status=StepStatus.RUNNING,
    )
    running = _state(ImportStatus.APPLYING, plan=_plan(steps=[app]))
    running.current_step = 1
    done = _state(ImportStatus.COMPLETED, plan=_plan(steps=[app]), position=1)
    body = PluginInstallResponse.from_install(
        Install(install_id=uuid4(), pod_id=uuid4(), imports=[running, done])
    )
    first, second = body.plugins
    assert body.finished is False
    assert first.current_step is not None and first.current_step.name == "inbox"
    assert first.app is None
    assert second.app == "inbox"


# --- the request --------------------------------------------------------------


def test_a_plugin_named_twice_is_refused():
    with pytest.raises(ValidationError):
        PluginInstallRequest(
            plugins=[
                {
                    "owner": "lemma-work",
                    "repo": "awesome-lemma",
                    "path": "install/mail",
                },
                {
                    "repo_url": "https://github.com/lemma-work/awesome-lemma",
                    "path": "install/mail",
                },
            ]
        )


def test_a_plugin_must_name_its_repository():
    with pytest.raises(ValidationError):
        PluginInstallRequest(plugins=[{"path": "install/mail"}])


# --- starting and retrying ----------------------------------------------------


class FakeStore:
    def __init__(self):
        self.staged: list[tuple[ImportState, bool]] = []

    async def stage_import(self, uow, state: ImportState, *, reopen=False):
        self.staged.append((state.model_copy(deep=True), reopen))


class FakeQueue:
    def __init__(self):
        self.calls: list[tuple[str, dict, str, object]] = []

    async def queue_job(self, uow, kind, payload, *, pod_id, dedup_key, delay=None):
        self.calls.append((kind, payload.model_dump(mode="json"), dedup_key, delay))
        return True


class FakeLimiter:
    def __init__(self):
        self.counted = 0

    async def check_and_increment(self, *, user_id, operation, limit):
        assert operation == "import"
        self.counted += 1


class FakeUow:
    session = object()

    async def commit(self):
        return None


class FakeUowFactory:
    def __call__(self):
        class _Ctx:
            async def __aenter__(self):
                return FakeUow()

            async def __aexit__(self, *a):
                return False

        return _Ctx()


async def _allowed(*, pod_id, user_id, action):
    """The controller's pod guard is the real check; here, only the plumbing."""


def _use_cases(installed: list[ImportState] | None = None):
    store, queue, limiter = FakeStore(), FakeQueue(), FakeLimiter()

    async def read(*, pod_id, install_id):
        return list(installed or [])

    uc = InstallUseCases(
        FakeUowFactory(),
        state_store=store,
        job_queue=queue,
        rate_limiter=limiter,
        read_install=read,
        authorize=_allowed,
    )
    return uc, store, queue, limiter


async def test_starting_queues_every_plugin_in_the_order_named():
    uc, store, queue, limiter = _use_cases()
    install = await uc.start_install(
        pod_id=uuid4(),
        user_id=uuid4(),
        sources=[
            PluginSource(owner="lemma-work", repo="awesome-lemma", path="install/mail"),
            PluginSource(
                repo_url="https://github.com/lemma-work/awesome-lemma",
                path="install/meetings",
            ),
        ],
    )
    assert limiter.counted == 2
    assert [s.install.position for s in install.imports] == [0, 1]
    assert {s.install.install_id for s in install.imports} == {install.install_id}
    assert all(s.status is ImportStatus.QUEUED for s in install.imports)
    assert [state.source.path for state, _ in store.staged] == [
        "install/mail",
        "install/meetings",
    ]
    assert [(kind, key, delay) for kind, _, key, delay in queue.calls] == [
        (INSTALL_PLUGIN, plan_key(install.imports[0].import_id), install_delay(0)),
        (INSTALL_PLUGIN, plan_key(install.imports[1].import_id), install_delay(1)),
    ]
    assert install_delay(0) < install_delay(1)
    assert {(p["owner"], p["repo"]) for _, p, _, _ in queue.calls} == {
        ("lemma-work", "awesome-lemma")
    }


async def test_a_retry_queues_only_the_failed_plugins_where_they_stopped():
    user_id, install_id = uuid4(), uuid4()
    step = PlanStep(
        index=0,
        kind=StepKind.TABLE,
        name="threads",
        action=StepAction.CREATE,
        status=StepStatus.FAILED,
    )
    planned = _state(
        ImportStatus.FAILED,
        plan=_plan(steps=[step]),
        error="Step 'threads' failed.",
        install_id=install_id,
        user_id=user_id,
    )
    unplanned = _state(
        ImportStatus.FAILED,
        position=1,
        error="Not found.",
        install_id=install_id,
        user_id=user_id,
    )
    installed = _state(
        ImportStatus.COMPLETED, position=2, install_id=install_id, user_id=user_id
    )
    uc, store, queue, _ = _use_cases([planned, unplanned, installed])

    await uc.retry_install(pod_id=uuid4(), install_id=install_id, user_id=user_id)

    reopened = {state.import_id: (state, reopen) for state, reopen in store.staged}
    assert set(reopened) == {planned.import_id, unplanned.import_id}
    again, reopen = reopened[planned.import_id]
    assert reopen and again.attempt == 2 and again.error is None
    # Approved and applied again by the worker; its steps are kept.
    assert again.status is ImportStatus.AWAITING_CONFIRMATION
    assert again.plan is not None and again.plan.steps[0].status is StepStatus.FAILED
    # Nothing of it was applied: from the repository again.
    assert reopened[unplanned.import_id][0].status is ImportStatus.QUEUED
    assert [key for _, _, key, _ in queue.calls] == [
        plan_key(planned.import_id),
        plan_key(unplanned.import_id),
    ]


async def test_an_install_is_its_starters_alone():
    install_id = uuid4()
    theirs = _state(ImportStatus.QUEUED, install_id=install_id)
    uc, *_ = _use_cases([theirs])
    with pytest.raises(BundleJobNotFoundError):
        await uc.get_install(pod_id=uuid4(), install_id=install_id, user_id=uuid4())
    found = await uc.get_install(
        pod_id=uuid4(), install_id=install_id, user_id=theirs.user_id
    )
    assert found.imports == [theirs]
