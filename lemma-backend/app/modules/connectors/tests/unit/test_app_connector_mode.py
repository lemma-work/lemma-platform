"""APP pod connectors: the organization's GitHub App, with nobody's account.

What decides an APP connector's behaviour without a database: which
installations count, which one a call acts on, what declaring one may be, that
everything needing an account refuses it, and how a call on one is planned,
presented and recorded. The HTTP journeys -- declaring from an app page,
executing through the route, a schedule firing on the App's webhook -- are in
``tests/e2e/test_app_connector_e2e.py``.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.core.analytics import MemorySink, configure
from app.modules.connectors.api.schemas.pod_connector_schemas import (
    PodConnectorCreateRequest,
)
from app.modules.connectors.domain.analytics import operation_execution_recorded
from app.modules.connectors.domain.app_identity import (
    TOKEN_REFUSED,
    AppIdentityUnavailableError,
    note_presented,
)
from app.modules.connectors.domain.app_installation import (
    AppInstallation,
    app_is_installed,
    pick_installation,
    requested_owner,
)
from app.modules.connectors.domain.auth_config import AuthConfigEntity
from app.modules.connectors.domain.connector import ConnectorKind
from app.modules.connectors.domain.connector_operation import ResolvedOperation
from app.modules.connectors.domain.execution_plan import ResolvedConnectorExecution
from app.modules.connectors.domain.kinds import ExecutionRequest
from app.modules.connectors.domain.pod_connector import (
    PodConnectorEntity,
    PodConnectorMode,
    PodConnectorStatus,
    PodConnectorVisibility,
    connector_status,
)
from app.modules.connectors.domain.pod_connector_errors import (
    AppConnectorUnsupportedError,
    AppInstallationAmbiguousError,
    AppNotInstalledError,
    ConnectorUnavailableError,
    PodConnectorValidationError,
)
from app.modules.connectors.services.auth.github_app_installations import (
    ask_github,
    checked_app_installations,
)
from app.modules.connectors.services.execution.github_presenter import (
    GitHubCredentialPresenter,
)
from app.modules.connectors.services.operation_visibility import offered_by_mode
from app.modules.connectors.services.pod_account_rule import (
    AccountAsk,
    PodAccountPicker,
    ResolvedAppConnector,
)
from app.modules.connectors.services.pod_connector_operations import (
    _app_plan,
    app_refusals_translated,
    with_app_installation,
)
from app.modules.connectors.services.pod_connector_use import (
    can_use,
    slug_to_declare,
)

ORG = uuid4()
POD = uuid4()

LEMMA = AppInstallation("101", "lemma-platform", "Organization", "selected", True)
ACME = AppInstallation("202", "acme", "Organization", "all", True)
PERSONAL = AppInstallation("303", "ada-lovelace", "User", "all", True)
UNCHECKED = AppInstallation("404")


def _github(**overrides: object) -> AuthConfigEntity:
    fields: dict[str, object] = {
        "organization_id": ORG,
        "connector_id": "github",
        "name": "github",
        **overrides,
    }
    return AuthConfigEntity(**fields)  # type: ignore[arg-type]


def _connector(mode: PodConnectorMode, **overrides: object) -> PodConnectorEntity:
    return PodConnectorEntity(
        pod_id=POD,
        slug=str(overrides.pop("slug", "github")),
        auth_config_id=uuid4(),
        mode=mode,
        **overrides,  # type: ignore[arg-type]
    )


# -- which installations count ------------------------------------------------


def test_only_a_checked_installation_on_a_github_organization_counts() -> None:
    """A personal installation would hand someone's own repositories to every
    editor of a shared Lemma; one GitHub was never asked about might be one."""
    assert LEMMA.counts
    assert not PERSONAL.counts
    assert not UNCHECKED.counts
    assert UNCHECKED.may_count and not PERSONAL.may_count


def test_an_app_connector_needs_an_installation_that_counts() -> None:
    connector = _connector(PodConnectorMode.APP)
    status = connector_status(
        connector,
        installation_active=True,
        shared_account=None,
        app_installed=app_is_installed((PERSONAL,)),
    )
    assert status is PodConnectorStatus.NEEDS_INSTALLATION
    assert (
        connector_status(
            connector,
            installation_active=True,
            shared_account=None,
            app_installed=app_is_installed((PERSONAL, LEMMA)),
        )
        is PodConnectorStatus.READY
    )
    assert (
        connector_status(
            connector,
            installation_active=False,
            shared_account=None,
            app_installed=True,
        )
        is PodConnectorStatus.UNAVAILABLE
    )


# -- which one a call acts on -------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "owner"),
    [
        ({"owner": "acme", "repo": "x"}, "acme"),
        ({"org": "acme"}, "acme"),
        ({"repository": "acme/widgets"}, "acme"),
        ({"full_name": "Acme/widgets"}, "Acme"),
        ({"repo": "widgets"}, None),
        ({}, None),
    ],
)
def test_the_owner_a_call_names(payload: dict[str, object], owner: str | None) -> None:
    assert requested_owner(payload) == owner


def test_the_owner_picks_its_installation_case_insensitively() -> None:
    picked = pick_installation(
        (LEMMA, ACME), owner="ACME", slug="github", install_url=None
    )
    assert picked is ACME


def test_with_one_installation_a_call_that_names_no_owner_uses_it() -> None:
    assert pick_installation((LEMMA,), owner=None, slug="github", install_url=None) is (
        LEMMA
    )


def test_several_installations_and_no_owner_is_refused_with_the_logins() -> None:
    with pytest.raises(AppInstallationAmbiguousError) as refused:
        pick_installation((LEMMA, ACME), owner=None, slug="github", install_url=None)
    assert refused.value.code == "APP_INSTALLATION_AMBIGUOUS"
    assert refused.value.status_code == 409
    assert refused.value.details["logins"] == ["acme", "lemma-platform"]


def test_an_owner_the_app_is_not_on_is_refused_with_where_to_install() -> None:
    with pytest.raises(AppNotInstalledError) as refused:
        pick_installation(
            (LEMMA,),
            owner="globex",
            slug="github",
            install_url="https://github.com/apps/lemma/installations/new",
        )
    assert refused.value.code == "APP_NOT_INSTALLED"
    assert refused.value.details == {
        "pod_connector": "github",
        "owner": "globex",
        "install_url": "https://github.com/apps/lemma/installations/new",
    }


def test_a_personal_installation_is_never_picked_even_by_its_owner() -> None:
    with pytest.raises(AppNotInstalledError):
        pick_installation(
            (PERSONAL, LEMMA), owner="ada-lovelace", slug="github", install_url=None
        )


async def test_an_unchecked_installation_is_asked_about_before_a_call_picks() -> None:
    asked: list[str] = []

    async def fetch(installation_id: str) -> AppInstallation | None:
        asked.append(installation_id)
        return AppInstallation(installation_id, "globex", "Organization", "all", True)

    checked = await checked_app_installations((LEMMA, UNCHECKED), fetch=fetch)

    assert asked == ["404"]
    assert [one.account_login for one in checked] == ["lemma-platform", "globex"]


async def test_installations_are_asked_about_together_not_one_after_another() -> None:
    """Fifty installations asked in turn, each allowed seconds, once held the
    Connections page for minutes. Asked together, the read takes as long as
    the slowest answer, not the sum."""

    async def fetch(installation_id: str) -> AppInstallation | None:
        await asyncio.sleep(0.2)
        return AppInstallation(
            installation_id, "o" + installation_id, "Organization", "all", True
        )

    started = time.monotonic()
    answers = await ask_github([str(i) for i in range(8)], fetch=fetch)

    assert sorted(answers) == [str(i) for i in range(8)]
    assert time.monotonic() - started < 1.0


async def test_an_installation_github_is_slow_on_keeps_what_was_known() -> None:
    """Past the budget the page stops waiting: the slow one stays as it was
    and is asked again on the next read; the quick one is used."""

    async def fetch(installation_id: str) -> AppInstallation | None:
        if installation_id == "404":
            await asyncio.sleep(5)
        return AppInstallation(installation_id, "globex", "Organization", "all", True)

    checked = await checked_app_installations(
        (UNCHECKED, AppInstallation("405")), fetch=fetch, budget=timedelta(seconds=0.2)
    )

    assert checked[0] == UNCHECKED
    assert checked[1].checked and checked[1].account_login == "globex"


async def test_an_installation_github_fails_on_is_left_out_quietly() -> None:
    async def fetch(installation_id: str) -> AppInstallation | None:
        raise RuntimeError("GitHub fell over")

    assert await ask_github(["404"], fetch=fetch) == {}


# -- declaring one ------------------------------------------------------------


class _Declared:
    def __init__(self, *slugs: str) -> None:
        self.slugs = set(slugs)

    async def list_by_pod(self, _pod_id: UUID) -> list[SimpleNamespace]:
        return [SimpleNamespace(slug=slug) for slug in self.slugs]

    async def get_by_slug(self, _pod_id: UUID, slug: str) -> object | None:
        return SimpleNamespace(slug=slug) if slug in self.slugs else None


def _app_request(**overrides: object) -> PodConnectorCreateRequest:
    return PodConnectorCreateRequest(
        auth_config_id=uuid4(),
        mode=PodConnectorMode.APP,
        **overrides,  # type: ignore[arg-type]
    )


async def test_an_app_connector_is_called_github_when_that_is_free() -> None:
    slug = await slug_to_declare(_Declared(), POD, _github(), _app_request())  # type: ignore[arg-type]
    assert slug == "github"


async def test_an_app_connector_beside_a_per_member_github_is_github_bot() -> None:
    declared = _Declared("github", "github-bot")
    slug = await slug_to_declare(declared, POD, _github(), _app_request())  # type: ignore[arg-type]
    assert slug == "github-bot-2"


@pytest.mark.parametrize(
    ("auth_config", "request_overrides", "reason"),
    [
        (_github(connector_id="gmail"), {}, "app_mode_github_only"),
        (_github(), {"shared_pod_account_id": uuid4()}, "app_mode_no_shared_account"),
        (
            _github(),
            {"visibility": PodConnectorVisibility.RESTRICTED},
            "app_mode_pod_visibility_only",
        ),
    ],
)
async def test_what_an_app_connector_may_not_be(
    auth_config: AuthConfigEntity, request_overrides: dict[str, object], reason: str
) -> None:
    with pytest.raises(PodConnectorValidationError) as refused:
        await slug_to_declare(
            _Declared(),  # type: ignore[arg-type]
            POD,
            auth_config,
            _app_request(**request_overrides),
        )
    assert refused.value.details == {"reason": reason}


class _Ctx:
    def __init__(self, *, may_use: bool, person: UUID | None = None) -> None:
        self._may_use = may_use
        self.actor = SimpleNamespace()
        self.delegating_user_id = person
        self.user_id = person

    async def can(self, _permission: str, _resource: object) -> bool:
        return self._may_use


async def test_an_app_connector_serves_whoever_holds_connector_use() -> None:
    """A schedule, a webhook, a contact: nobody behind the call is no reason
    to refuse when the connector runs as nobody anyway."""
    connector = _connector(PodConnectorMode.APP)
    ready = PodConnectorStatus.READY
    assert await can_use(_Ctx(may_use=True), connector, ready, None)  # type: ignore[arg-type]
    assert not await can_use(_Ctx(may_use=False), connector, ready, None)  # type: ignore[arg-type]
    assert not await can_use(
        _Ctx(may_use=True),  # type: ignore[arg-type]
        connector,
        PodConnectorStatus.NEEDS_INSTALLATION,
        None,
    )


# -- everything that needs an account refuses it ------------------------------


class _NoReads:
    async def get(self, _id: UUID) -> None:
        raise AssertionError("an APP connector reads no account")


def _picker(*installations: AppInstallation) -> PodAccountPicker:
    async def app_installations(_org: UUID) -> tuple[AppInstallation, ...]:
        return installations

    async def is_member(_pod: UUID, _user: UUID) -> bool:
        return True

    return PodAccountPicker(
        pod_accounts=_NoReads(),  # type: ignore[arg-type]
        accounts=_NoReads(),  # type: ignore[arg-type]
        is_pod_member=is_member,
        app_installations=app_installations,
    )


async def _no_shared_check(_pod_account: object) -> None:
    return None


async def test_an_account_reader_asking_for_an_app_connector_is_refused() -> None:
    """A surface's credential, the sandbox's git, bundle publishing: each reads
    the account, and an APP connector has none to give."""
    with pytest.raises(AppConnectorUnsupportedError) as refused:
        await _picker(LEMMA).pick(
            _connector(PodConnectorMode.APP, slug="bot"),
            _github(),
            AccountAsk(person=uuid4(), shared_check=_no_shared_check),
        )
    assert refused.value.code == "APP_CONNECTOR_UNSUPPORTED"
    assert refused.value.status_code == 409
    assert refused.value.details == {"pod_connector": "bot"}


async def test_operations_and_schedules_get_the_app_and_its_installations() -> None:
    resolved = await _picker(LEMMA, ACME).pick_any(
        _connector(PodConnectorMode.APP),
        _github(),
        AccountAsk(person=None, shared_check=_no_shared_check),
    )
    assert isinstance(resolved, ResolvedAppConnector)
    assert resolved.app_installations == (LEMMA, ACME)


# -- planning, presenting, recording a call -----------------------------------


def _operation(token_kind: str) -> SimpleNamespace:
    return SimpleNamespace(
        name="issues_create",
        execution_name="issues_create",
        execution={"github_token_kind": token_kind},
        input_schema={},
        output_schema={},
    )


def _service(operation: SimpleNamespace) -> SimpleNamespace:
    async def operation_for(_auth_config: object, _name: str) -> SimpleNamespace:
        return operation

    return SimpleNamespace(operation_for=operation_for)


def _resolved_app(*installations: AppInstallation) -> ResolvedAppConnector:
    return ResolvedAppConnector(
        pod_connector=_connector(PodConnectorMode.APP),
        auth_config=_github(),
        app_installations=installations,
    )


async def test_a_call_on_an_app_connector_is_the_app_whatever_was_asked() -> None:
    person = uuid4()
    plan = await _app_plan(
        _service(_operation("installation_ok")),  # type: ignore[arg-type]
        SimpleNamespace(delegating_user_id=None, user_id=person),  # type: ignore[arg-type]
        _resolved_app(LEMMA),
        operation_name="issues_create",
        payload={"owner": "lemma-platform"},
    )
    assert plan.act_as == "app_only"
    assert plan.third_party_credentials == {}
    assert plan.account_id is None and plan.account_user_id is None
    assert plan.organization_id == ORG
    assert plan.acting_user_id == person
    assert plan.pod_connector == "github"
    assert plan.app_installations == (LEMMA,)


async def test_a_route_github_opens_only_to_a_person_is_refused_with_why() -> None:
    with pytest.raises(AppIdentityUnavailableError) as refused:
        await _app_plan(
            _service(_operation("user_only")),  # type: ignore[arg-type]
            SimpleNamespace(delegating_user_id=None, user_id=uuid4()),  # type: ignore[arg-type]
            _resolved_app(LEMMA),
            operation_name="gists_create",
            payload={},
        )
    assert refused.value.code == "APP_IDENTITY_UNAVAILABLE"
    assert refused.value.details == {
        "reason": "user_only_operation",
        "pod_connector": "github",
    }


def _plan(**overrides: object) -> ResolvedConnectorExecution:
    base = ResolvedConnectorExecution(
        connector_id="github",
        operation_execution_name="issues_create",
        provider="native",
        third_party_credentials={},
        payload={"repository": "acme/widgets"},
        organization_id=ORG,
        acting_user_id=uuid4(),
        act_as="app_only",
        pod_connector="github",
        app_installations=(LEMMA, ACME),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


async def test_the_execute_phase_acts_on_the_payloads_installation() -> None:
    picked = await with_app_installation(_plan())
    assert picked.account_external_ref == "202"


async def test_a_plan_on_an_account_is_left_alone() -> None:
    plan = _plan(app_installations=None, account_external_ref="999")
    assert await with_app_installation(plan) is plan


def test_github_refusing_the_app_a_token_is_an_unavailable_connector() -> None:
    with pytest.raises(ConnectorUnavailableError) as refused:
        with app_refusals_translated(_plan()):
            raise AppIdentityUnavailableError("no token", code=TOKEN_REFUSED)
    assert refused.value.code == "CONNECTOR_UNAVAILABLE"
    assert refused.value.details == {
        "pod_connector": "github",
        "reason": "app_token_refused",
    }


async def test_the_presenter_refuses_a_person_only_route_with_its_reason() -> None:
    request = ExecutionRequest(
        connector_id="github",
        kind=ConnectorKind.HTTP,
        operation=ResolvedOperation(
            name="gists_create", execution={"github_token_kind": "user_only"}
        ),
        payload={},
        credentials={},
        config={},
        deadline_seconds=45.0,
        account_external_ref="101",
        act_as="app_only",
    )
    with pytest.raises(AppIdentityUnavailableError) as refused:
        await GitHubCredentialPresenter().present(request)
    assert refused.value.details == {"reason": "user_only_operation"}


def test_listings_for_an_app_connector_leave_out_person_only_routes() -> None:
    operations = [
        SimpleNamespace(name="issues_create", execution={"github_token_kind": "x"}),
        SimpleNamespace(
            name="gists_create", execution={"github_token_kind": "user_only"}
        ),
        SimpleNamespace(name="plain", execution=None),
    ]
    assert [one.name for one in offered_by_mode(operations, PodConnectorMode.APP)] == [
        "issues_create",
        "plain",
    ]
    assert offered_by_mode(operations, PodConnectorMode.PER_MEMBER) == operations


@pytest.fixture
def sink():
    memory = MemorySink()
    configure(memory, deployment="test", strict=True)
    yield memory
    configure(None)


def test_the_record_says_the_call_went_out_as_the_app(sink: MemorySink) -> None:
    plan = _plan(account_external_ref="202")
    with operation_execution_recorded(plan):
        note_presented("app")

    (event,) = sink.events
    assert event.name == "connector.operation_executed"
    assert event.properties["identity"] == "app"
    assert event.properties["pod_connector"] == "github"
    assert event.properties["installation_id"] == "202"


def test_the_record_says_what_was_presented_not_what_was_asked(
    sink: MemorySink,
) -> None:
    """An agent asks for the app on a per-member connector; GitHub routes that
    take only a person get the person's token, and the record says so."""
    plan = _plan(app_installations=None, act_as="app", account_external_ref="101")
    with operation_execution_recorded(plan):
        note_presented("user")

    (event,) = sink.events
    assert event.properties["identity"] == "user"
    assert "installation_id" not in event.properties
