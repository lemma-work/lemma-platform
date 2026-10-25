"""Whether a teammate's model can run, decided before a run depends on it.

The check behind save-time validation, turn start and retry: a profile that is
gone, another member's own runtime, a model the catalog no longer lists, and a
model a provider just answered 404 for are each refused with their own code --
never swapped for another model. A member's own runtime passes for them.
"""

from __future__ import annotations

from contextlib import nullcontext as does_not_raise
from uuid import UUID, uuid4

import pytest

from app.modules.agent.domain.runtime_profiles import (
    RuntimeProfileProtocol,
    RuntimeProfileScope,
)
from app.modules.agent.domain.runtime_snapshot import RuntimeSnapshot
from app.modules.agent.domain.errors import RuntimeUnavailableError
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    OpenAICompatibleRuntimeConfig,
    RuntimeModelCatalogEntry,
    RuntimeProfileKind,
)
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.infrastructure.unavailable_models import (
    UnavailableModels,
    remember_provider_refusal,
)
from app.modules.agent.services.runtime_availability import (
    SelectedModel,
    check_runtime_selection,
    refuse_a_model_the_provider_refused,
)
from app.modules.agent.services.runtime_profile_service import (
    AgentRuntimeProfileService,
)
from app.modules.agent.domain.resolved_runtime import RuntimeHolder

pytestmark = pytest.mark.unit


class _Cache:
    """The two RedisJsonCache calls the store makes, in memory."""

    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    async def get_json(self, suffix: str) -> object | None:
        return self.values.get(suffix)

    async def set_json(self, suffix: str, value: object) -> None:
        self.values[suffix] = value


class _Profiles:
    """The repository's reads, with its visibility rule."""

    def __init__(
        self, *profiles: AgentRuntimeProfile, first_names: dict[UUID, str] | None = None
    ) -> None:
        self._profiles = {profile.id: profile for profile in profiles}
        self._first_names = first_names or {}

    async def get_personal_by_id(
        self, *, profile_id: str, organization_id: UUID
    ) -> AgentRuntimeProfile | None:
        profile = self._profiles.get(profile_id)
        if profile is None or profile.organization_id != organization_id:
            return None
        return profile if profile.scope is RuntimeProfileScope.PERSONAL else None

    async def owner_first_name(self, user_id: UUID) -> str | None:
        return self._first_names.get(user_id)

    async def get_visible_by_id(
        self, *, profile_id: str, organization_id: UUID, user_id: UUID
    ) -> AgentRuntimeProfile | None:
        profile = self._profiles.get(profile_id)
        if profile is None or profile.organization_id != organization_id:
            return None
        if profile.scope is RuntimeProfileScope.PERSONAL and profile.user_id != user_id:
            return None
        return profile


def _profile(
    organization_id: UUID,
    *,
    scope: RuntimeProfileScope = RuntimeProfileScope.ORGANIZATION,
    user_id: UUID | None = None,
) -> AgentRuntimeProfile:
    return AgentRuntimeProfile(
        id=str(uuid4()),
        organization_id=organization_id,
        user_id=user_id,
        scope=scope,
        kind=RuntimeProfileKind.MODEL_PROVIDER,
        protocol=RuntimeProfileProtocol.OPENAI_COMPATIBLE,
        name="Vendor",
        default_model_name="kept",
        model_catalog=[
            RuntimeModelCatalogEntry(name="kept", provider_model_name="kept")
        ],
        config=OpenAICompatibleRuntimeConfig(base_url="https://api.example.com/v1"),
    )


async def _refusal(
    runtime: AgentRuntimeConfig,
    *profiles: AgentRuntimeProfile,
    organization_id: UUID,
    user_id: UUID,
    first_names: dict[UUID, str] | None = None,
) -> RuntimeUnavailableError:
    service = AgentRuntimeProfileService(_Profiles(*profiles, first_names=first_names))  # type: ignore[arg-type]  # TST-03: the repository reads the check makes
    with pytest.raises(RuntimeUnavailableError) as refused:
        await check_runtime_selection(
            runtime,
            profiles=service,
            organization_id=organization_id,
            user_id=user_id,
        )
    return refused.value


@pytest.mark.asyncio
async def test_a_model_the_catalog_no_longer_lists_is_refused() -> None:
    org = uuid4()
    profile = _profile(org)

    refused = await _refusal(
        AgentRuntimeConfig(profile_id=profile.id, model_name="retired"),
        profile,
        organization_id=org,
        user_id=uuid4(),
    )

    assert refused.code == "model_unavailable"
    assert refused.status_code == 422
    # A turn: the pin was accepted when saved, so the model went away since.
    assert "Vendor no longer offers retired" in refused.message
    assert "this conversation" in refused.message


@pytest.mark.asyncio
@pytest.mark.parametrize("holder", list(RuntimeHolder))
async def test_a_save_naming_a_model_does_not_claim_it_was_ever_offered(
    holder: RuntimeHolder,
) -> None:
    """A save can name a model that never existed; "no longer" would be false,
    and the thing being saved is named as what it is."""
    org = uuid4()
    profile = _profile(org)
    service = AgentRuntimeProfileService(_Profiles(profile))  # type: ignore[arg-type]  # TST-03: the repository reads the check makes

    with pytest.raises(RuntimeUnavailableError) as refused:
        await check_runtime_selection(
            AgentRuntimeConfig(profile_id=profile.id, model_name="imaginary"),
            profiles=service,
            organization_id=org,
            user_id=uuid4(),
            saving_for=holder,
        )

    message = refused.value.message
    assert refused.value.code == "model_unavailable"
    assert "Vendor does not offer a model named imaginary" in message
    assert "no longer" not in message
    assert f"for this {holder.value}" in message
    other = next(h for h in RuntimeHolder if h is not holder)
    assert other.value not in message


def test_a_concurrent_provider_edit_is_not_called_a_model() -> None:
    from app.modules.agent.domain.errors import RuntimeProfileConflictError

    conflict = RuntimeProfileConflictError()

    assert conflict.code == "RUNTIME_PROFILE_CONFLICT"
    assert "provider settings" in conflict.message
    assert "model" not in conflict.message.lower()


@pytest.mark.asyncio
async def test_a_profile_nobody_here_can_see_is_refused() -> None:
    org = uuid4()

    refused = await _refusal(
        AgentRuntimeConfig(profile_id=str(uuid4())),
        organization_id=org,
        user_id=uuid4(),
    )

    assert refused.code == "runtime_profile_unavailable"


@pytest.mark.asyncio
async def test_a_member_may_run_on_their_own_personal_profile() -> None:
    org, owner = uuid4(), uuid4()
    personal = _profile(org, scope=RuntimeProfileScope.PERSONAL, user_id=owner)

    selected = await check_runtime_selection(
        AgentRuntimeConfig(profile_id=personal.id, model_name="kept"),
        profiles=AgentRuntimeProfileService(_Profiles(personal)),  # type: ignore[arg-type]  # TST-03: as above
        organization_id=org,
        user_id=owner,
    )

    assert selected == SelectedModel(
        profile_id=personal.id, profile_name="Vendor", model_name="kept"
    )


@pytest.mark.asyncio
async def test_anyone_else_reaching_a_members_own_profile_is_told_whose_it_is() -> None:
    org, owner = uuid4(), uuid4()
    personal = _profile(org, scope=RuntimeProfileScope.PERSONAL, user_id=owner)

    refused = await _refusal(
        AgentRuntimeConfig(profile_id=personal.id, model_name="kept"),
        personal,
        organization_id=org,
        user_id=uuid4(),
        first_names={owner: "Ada"},
    )

    assert refused.code == "runtime_profile_not_shareable"
    assert refused.status_code == 422
    assert "Ada's own Vendor" in refused.message


@pytest.mark.asyncio
async def test_a_model_the_provider_just_404d_is_refused_without_asking_again() -> None:
    unavailable = UnavailableModels(_Cache())  # type: ignore[arg-type]  # TST-03: the two cache calls the store makes
    await unavailable.mark(profile_id="p", model_name="kept")

    with pytest.raises(RuntimeUnavailableError) as refused:
        await refuse_a_model_the_provider_refused(
            SelectedModel(profile_id="p", profile_name="Vendor", model_name="kept"),
            unavailable,
        )

    assert refused.value.code == "model_unavailable"


@pytest.mark.asyncio
async def test_a_listed_model_on_a_shared_profile_passes_and_is_named() -> None:
    org = uuid4()
    profile = _profile(org)

    selected = await check_runtime_selection(
        AgentRuntimeConfig(profile_id=profile.id, model_name="kept"),
        profiles=AgentRuntimeProfileService(_Profiles(profile)),  # type: ignore[arg-type]  # TST-03: as above
        organization_id=org,
        user_id=uuid4(),
    )

    assert selected == SelectedModel(
        profile_id=profile.id, profile_name="Vendor", model_name="kept"
    )
    with does_not_raise():
        await refuse_a_model_the_provider_refused(
            selected,
            UnavailableModels(_Cache()),  # type: ignore[arg-type]  # TST-03: as above
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(("status_code", "remembered"), [(404, True), (500, False)])
async def test_only_a_provider_404_is_remembered(
    status_code: int, remembered: bool
) -> None:
    cache = _Cache()

    await remember_provider_refusal(
        RuntimeSnapshot(
            profile_id="p",
            profile_name="p",
            scope=RuntimeProfileScope.ORGANIZATION,
            protocol=RuntimeProfileProtocol.OPENAI_COMPATIBLE,
            model_name="m",
        ),
        status_code=status_code,
        store=UnavailableModels(cache),  # type: ignore[arg-type]  # TST-03: in-memory cache
    )

    assert (cache.values != {}) is remembered
