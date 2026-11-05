"""A stored provider key only ever goes to an address it was entered for.

The key is write-only, so an edit that moved the profile to another host and
kept the key would send it there on the next discovery, run or test -- reading
it back by another route.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    AnthropicCompatibleRuntimeConfig,
    ApiKeyRuntimeCredentials,
    OpenAICompatibleRuntimeConfig,
    RuntimeModelCatalogEntry,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
    reveal_credentials,
)
from app.modules.agent.domain.sentinels import UNSET
from app.modules.agent.domain.runtime_provider_patch import (
    KeyMustBeReenteredError,
    ProviderProfilePatch,
    provider_origin,
    resolve_provider_patch,
)

pytestmark = pytest.mark.unit

_STORED_URL = "https://api.vendor.test/v1"


def _profile(
    *,
    protocol: RuntimeProfileProtocol = RuntimeProfileProtocol.OPENAI_COMPATIBLE,
    config: OpenAICompatibleRuntimeConfig
    | AnthropicCompatibleRuntimeConfig
    | None = None,
    with_key: bool = True,
) -> AgentRuntimeProfile:
    return AgentRuntimeProfile(
        id=str(uuid4()),
        organization_id=uuid4(),
        scope=RuntimeProfileScope.ORGANIZATION,
        kind=RuntimeProfileKind.MODEL_PROVIDER,
        protocol=protocol,
        name="Vendor",
        default_model_name="model-a",
        model_catalog=[
            RuntimeModelCatalogEntry(name="model-a", provider_model_name="model-a")
        ],
        config=config or OpenAICompatibleRuntimeConfig(base_url=_STORED_URL),
        credentials=ApiKeyRuntimeCredentials(api_key="sk-stored") if with_key else None,
    )


def _patch(profile: AgentRuntimeProfile, **fields: object) -> ProviderProfilePatch:
    request: dict[str, object] = {
        "name": UNSET,
        "description": UNSET,
        "base_url": UNSET,
        "api_key": UNSET,
        "model_names": UNSET,
        "headers": UNSET,
        "model_settings": UNSET,
    }
    request.update(fields)
    return resolve_provider_patch(
        profile,
        profile.config,
        is_anthropic=profile.protocol is RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE,
        refresh_models=False,
        **request,
    )


@pytest.mark.parametrize(
    "new_url",
    [
        "https://attacker.example.com/v1",
        "http://api.vendor.test/v1",  # scheme
        "https://api.vendor.test:8443/v1",  # port
    ],
)
def test_moving_a_provider_to_another_origin_without_its_key_is_refused(
    new_url: str,
) -> None:
    with pytest.raises(KeyMustBeReenteredError):
        _patch(_profile(), base_url=new_url)


def test_moving_a_provider_with_a_fresh_key_sends_only_that_key() -> None:
    patch = _patch(
        _profile(), base_url="https://other.example.com/v1", api_key="sk-new"
    )

    assert patch.origin_changed is True
    assert patch.api_secret() == "sk-new"
    assert reveal_credentials(patch.credentials) == {"api_key": "sk-new"}


def test_a_new_path_on_the_same_host_keeps_the_stored_key() -> None:
    patch = _patch(_profile(), base_url="https://API.vendor.test:443/v2")

    assert patch.base_url_changed is True
    assert patch.origin_changed is False
    assert patch.api_secret() == "sk-stored"


def test_a_keyless_provider_may_move_freely() -> None:
    patch = _patch(_profile(with_key=False), base_url="http://127.0.0.1:11434/v1")

    assert patch.origin_changed is True
    assert patch.credentials is None


def test_stored_headers_do_not_follow_a_move_either() -> None:
    profile = _profile(
        with_key=False,
        config=OpenAICompatibleRuntimeConfig(
            base_url=_STORED_URL, headers={"Ocp-Apim-Subscription-Key": "secret"}
        ),
    )

    with pytest.raises(KeyMustBeReenteredError):
        _patch(profile, base_url="https://other.example.com/v1")

    moved = _patch(
        profile,
        base_url="https://other.example.com/v1",
        headers={"Ocp-Apim-Subscription-Key": "new-secret"},
    )
    assert moved.headers == {"Ocp-Apim-Subscription-Key": "new-secret"}


def test_clearing_an_anthropic_url_is_a_move_to_its_default_endpoint() -> None:
    profile = _profile(
        protocol=RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE,
        config=AnthropicCompatibleRuntimeConfig(base_url="https://proxy.example.com"),
    )

    with pytest.raises(KeyMustBeReenteredError):
        _patch(profile, base_url=None)


def test_provider_origin_ignores_path_and_default_ports() -> None:
    assert provider_origin(
        "https://Host.Example.com/v1", is_anthropic=False
    ) == provider_origin("https://host.example.com:443/other", is_anthropic=False)
    assert provider_origin(None, is_anthropic=True) == (
        "https",
        "api.anthropic.com",
        443,
    )
