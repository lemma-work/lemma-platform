"""A webhook schedule on an APP pod connector: it listens as the GitHub App.

The connector has no account. Its routing key is one of the organization's App
installations, picked by the GitHub account the schedule names; a delivery
matches it while the App is still installed, whoever its owner is now. The
end-to-end delivery is in ``connectors/tests/e2e/test_app_connector_e2e.py``.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.modules.connectors.contracts.app_connectors import (
    AppInstallationAmbiguousError,
    AppNotInstalledError,
    PodConnectorApp,
)
from app.modules.connectors.contracts.triggers import TriggerBinding
from app.modules.connectors.domain.app_installation import AppInstallation
from app.modules.schedule.domain.errors import ScheduleValidationError
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.infrastructure.adapters.external_schedule_writer import (
    ExternalScheduleWriterAdapter,
)
from app.modules.schedule.services import schedule_connector_policy
from app.modules.schedule.services.webhook_handler import _still_bound

LEMMA = AppInstallation("101", "lemma-platform", "Organization", "selected", True)
ACME = AppInstallation("202", "acme", "Organization", "all", True)
PERSONAL = AppInstallation("303", "ada-lovelace", "User", "all", True)


def _schedule(**config: object) -> ScheduleEntity:
    return ScheduleEntity(
        user_id=uuid4(),
        schedule_type=ScheduleType.WEBHOOK,
        pod_id=uuid4(),
        connector_trigger_id="github_pull_request",
        pod_connector_slug="github",
        config=dict(config),
    )


def _app(schedule: ScheduleEntity, *installations: AppInstallation) -> PodConnectorApp:
    assert schedule.pod_id is not None
    return PodConnectorApp(
        pod_id=schedule.pod_id,
        slug="github",
        auth_config_id=uuid4(),
        connector_id="github",
        organization_id=uuid4(),
        installations=installations,
    )


def _writer(app: PodConnectorApp, bound: list[dict[str, object]]):
    async def resolve_connector(_uow: object, _schedule: ScheduleEntity):
        return app

    async def resolve_app_binding(_uow: object, **kwargs: object) -> TriggerBinding:
        bound.append(kwargs)
        return TriggerBinding(
            connector_id="github",
            event_type="pull_request",
            installation_id=str(kwargs["installation_id"]),
        )

    async def refuse_account_binding(_uow: object, **_kwargs: object):
        raise AssertionError("an APP schedule binds no account")

    async def pod_organization(_pod_id: UUID) -> UUID:
        return app.organization_id

    return ExternalScheduleWriterAdapter(
        uow=None,  # type: ignore[arg-type]
        resolve_connector=resolve_connector,
        resolve_app_binding=resolve_app_binding,
        resolve_binding=refuse_account_binding,
        pod_organization=pod_organization,
    )


async def test_one_installation_routes_the_schedule_with_no_owner_named() -> None:
    schedule = _schedule()
    bound: list[dict[str, object]] = []

    provisioned = await _writer(_app(schedule, LEMMA), bound).create_provider_trigger(
        schedule
    )

    assert bound[0]["installation_id"] == "101"
    assert provisioned.bound_config["installation_id"] == "101"
    assert provisioned.bound_config["source"] == "github"


async def test_the_owner_the_schedule_names_picks_among_several() -> None:
    schedule = _schedule(repository="acme/widgets")
    bound: list[dict[str, object]] = []

    provisioned = await _writer(
        _app(schedule, LEMMA, ACME), bound
    ).create_provider_trigger(schedule)

    assert provisioned.bound_config["installation_id"] == "202"


async def test_several_installations_and_no_owner_is_refused() -> None:
    schedule = _schedule()
    with pytest.raises(AppInstallationAmbiguousError):
        await _writer(_app(schedule, LEMMA, ACME), []).create_provider_trigger(schedule)


async def test_a_personal_installation_never_routes_a_shared_lemmas_schedule() -> None:
    schedule = _schedule()
    with pytest.raises(AppNotInstalledError):
        await _writer(_app(schedule, PERSONAL), []).create_provider_trigger(schedule)


async def test_an_mcp_event_on_an_app_connector_is_refused() -> None:
    schedule = _schedule(event="tasks.created")
    schedule = schedule.model_copy(update={"connector_trigger_id": None})
    writer = _writer(_app(schedule, LEMMA), [])
    with pytest.raises(ScheduleValidationError):
        await writer._provision_mcp_event(schedule)


async def test_a_delivery_matches_an_app_schedule_and_never_an_account_proof() -> None:
    schedule = _schedule()

    async def standing(_uow: object, _schedule: ScheduleEntity) -> str:
        return schedule_connector_policy.APP_IDENTITY

    assert await _still_bound(None, [schedule], None, standing) == [schedule]
    # A delivery a source proved with one account's secret is that account's.
    assert await _still_bound(None, [schedule], str(uuid4()), standing) == []


async def test_binding_an_app_connector_pins_no_account() -> None:
    schedule = _schedule()
    pinned_before: UUID | None = schedule.pod_account_id

    async def resolve(_uow: object, _ctx: object, **_kwargs: object) -> PodConnectorApp:
        return _app(schedule, LEMMA)

    bound = await schedule_connector_policy.require_connector_use(
        schedule,
        object(),  # type: ignore[arg-type]
        None,
        resolve=resolve,
    )
    assert bound.pod_account_id is None
    assert pinned_before is None
