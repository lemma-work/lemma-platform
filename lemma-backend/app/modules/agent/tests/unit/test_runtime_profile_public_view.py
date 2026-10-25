"""What any member of an organization may read of a provider profile.

The listing needs only membership. It used to redact by guessing secret key
names, which passed a key in the base URL's query, a password in its userinfo
and any header not named like a credential, while masking `max_tokens` -- a
mask an editor then saved back, breaking every run on the profile.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.api.runtime_profile_schemas import (
    AgentRuntimeProfileResponse,
)
from app.modules.agent.domain.errors import RuntimeProfileValidationError
from app.modules.agent.domain.organization_default import with_default_mark
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    HarnessRuntimeConfig,
    OpenAICompatibleRuntimeConfig,
    RuntimeModelCatalogEntry,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
    public_url,
)
from app.modules.agent.domain.sentinels import UNSET
from app.modules.agent.domain.value_objects import HarnessKind
from app.modules.agent.services.runtime_provider_patch import resolve_provider_patch
from app.modules.agent.domain.resolved_runtime import ResolvedAgentRuntime

pytestmark = pytest.mark.unit


def test_the_internal_runtime_snapshot_keeps_secrets_out_of_its_repr() -> None:
    """The unredacted snapshot is typed and internal; printing it, or the
    resolved runtime it came from, must not print a header or the key."""
    profile = _profile(
        OpenAICompatibleRuntimeConfig(
            base_url="https://api.example.com/v1",
            headers={"X-Gateway": "SECRET::header"},
        )
    )
    resolved = ResolvedAgentRuntime(
        profile=profile,
        harness_kind=HarnessKind.LEMMA,
        model=profile.model_catalog[0],
        provider_model_name="model-a",
        credentials={"api_key": "SECRET::key"},
    )

    snapshot = resolved.runtime_snapshot()

    assert snapshot.config["headers"] == {"X-Gateway": "SECRET::header"}
    assert "SECRET::" not in repr(snapshot)
    assert "SECRET::" not in repr(resolved)


def _profile(config: OpenAICompatibleRuntimeConfig):
    return AgentRuntimeProfile(
        id=str(uuid4()),
        organization_id=uuid4(),
        scope=RuntimeProfileScope.ORGANIZATION,
        kind=RuntimeProfileKind.MODEL_PROVIDER,
        protocol=RuntimeProfileProtocol.OPENAI_COMPATIBLE,
        name="Gateway",
        default_model_name="model-a",
        model_catalog=[
            RuntimeModelCatalogEntry(name="model-a", provider_model_name="model-a")
        ],
        config=config,
    )


def test_the_listing_strips_userinfo_and_query_from_the_base_url() -> None:
    profile = _profile(
        OpenAICompatibleRuntimeConfig(
            base_url="https://user:pw@proxy.example.com:8443/v1?key=abc#frag"
        )
    )

    config = profile.public_dict()["config"]

    assert config["base_url"] == "https://proxy.example.com:8443/v1"
    assert "pw" not in str(config) and "abc" not in str(config)


def test_the_listing_keeps_numeric_tuning_rather_than_masking_it() -> None:
    settings = {"max_tokens": 4096, "thinking": {"budget_tokens": 2048}}
    profile = _profile(
        OpenAICompatibleRuntimeConfig(
            base_url="https://api.example.com/v1", model_settings=settings
        )
    )

    assert profile.public_dict()["config"]["model_settings"] == settings


@pytest.mark.parametrize(
    "headers",
    [
        {"Ocp-Apim-Subscription-Key": "SECRET::apim"},
        {"X-Gateway": "SECRET::plain", "HTTP-Referer": "SECRET::referer"},
        {"Authorization": "SECRET::bearer", "x-tenant": "SECRET::tenant"},
    ],
)
def test_the_listing_never_carries_a_header_value(headers: dict[str, str]) -> None:
    profile = _profile(
        OpenAICompatibleRuntimeConfig(
            base_url="https://api.example.com/v1", headers=headers
        )
    )

    public = profile.public_dict()

    assert "SECRET::" not in repr(public)
    assert public["config"]["header_names"] == sorted(headers)


def test_public_url_keeps_an_ipv6_host_bracketed() -> None:
    assert public_url("http://u:p@[::1]:8080/v1?x=1") == "http://[::1]:8080/v1"


@pytest.mark.parametrize(
    "field,value",
    [
        ("model_settings", {"max_tokens": "<redacted>"}),
        ("model_settings", {"thinking": {"budget_tokens": "<redacted>"}}),
        ("headers", {"Authorization": "<redacted>"}),
    ],
)
def test_an_edit_carrying_the_old_mask_is_refused(
    field: str, value: dict[str, object]
) -> None:
    profile = _profile(OpenAICompatibleRuntimeConfig(base_url="https://a.example.com"))
    request: dict[str, object] = {
        "name": UNSET,
        "description": UNSET,
        "base_url": UNSET,
        "api_key": UNSET,
        "model_names": UNSET,
        "headers": UNSET,
        "model_settings": UNSET,
        field: value,
    }

    with pytest.raises(RuntimeProfileValidationError, match="<redacted>"):
        resolve_provider_patch(
            profile,
            profile.config,
            is_anthropic=False,
            refresh_models=False,
            **request,
        )


def test_the_listing_publishes_typed_fields_rather_than_the_metadata_bag() -> None:
    """Internal bookkeeping stays in; the two facts clients read come out typed."""
    profile = _profile(
        OpenAICompatibleRuntimeConfig(base_url="https://api.example.com/v1")
    ).with_changes(metadata={"source": "openai_compatible", "catalog_discovered": True})
    marked = with_default_mark(profile, model_name=None)

    unmarked_view = AgentRuntimeProfileResponse.model_validate(profile.public_dict())
    marked_view = AgentRuntimeProfileResponse.model_validate(marked.public_dict())

    assert "metadata" not in marked_view.model_dump()
    assert "catalog_discovered" not in str(marked_view.model_dump())
    assert unmarked_view.organization_default is False
    assert marked_view.organization_default is True
    assert marked_view.harness_key is None


def test_an_agent_host_profile_publishes_the_coding_agent_it_runs() -> None:
    profile = AgentRuntimeProfile(
        id=str(uuid4()),
        organization_id=uuid4(),
        user_id=uuid4(),
        harness_id=uuid4(),
        scope=RuntimeProfileScope.PERSONAL,
        kind=RuntimeProfileKind.HARNESS,
        protocol=RuntimeProfileProtocol.AGENT_HOST,
        name="Codex on my Mac",
        config=HarnessRuntimeConfig(harness_snapshot_revision="r1"),
        metadata={"source": "AGENT_HOST", "harness_key": "codex"},
    )

    view = AgentRuntimeProfileResponse.model_validate(profile.public_dict())

    assert view.harness_key == "codex"
    assert view.organization_default is False
