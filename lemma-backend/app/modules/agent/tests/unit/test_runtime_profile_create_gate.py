"""Which creates on the runtime profile route need the org edit gate.

The models page now renders inside a pod, where most callers are pod admins
holding no org role at all. Exactly one create is safe for them - registering a
computer they themselves paired - and the predicate below is what draws that
line, so it is worth pinning separately from the route.
"""

from uuid import uuid4

from app.modules.agent.api.controllers.runtime_config_controller import (
    _is_own_machine_profile,
)
from app.modules.agent.api.schemas import (
    CreateAgentHostRuntimeProfileRequest,
    CreateAnthropicCompatibleRuntimeProfileRequest,
    CreateOpenAICompatibleRuntimeProfileRequest,
)
from app.modules.agent.domain.runtime_profiles import RuntimeProfileScope


def test_personal_agent_host_profile_is_the_callers_own_machine():
    request = CreateAgentHostRuntimeProfileRequest(
        harness_id=uuid4(),
        name="My laptop",
    )

    # PERSONAL is the schema default: saying nothing is not offering a laptop
    # to the workspace.
    assert request.scope is RuntimeProfileScope.PERSONAL
    assert _is_own_machine_profile(request) is True


def test_sharing_a_machine_with_the_workspace_still_needs_the_org_gate():
    request = CreateAgentHostRuntimeProfileRequest(
        harness_id=uuid4(),
        name="Shared build box",
        scope=RuntimeProfileScope.ORGANIZATION,
    )

    assert _is_own_machine_profile(request) is False


def test_provider_credentials_are_never_personal():
    """A provider profile has no scope field - it is always org-wide."""
    openai = CreateOpenAICompatibleRuntimeProfileRequest(
        name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-test",
    )
    anthropic = CreateAnthropicCompatibleRuntimeProfileRequest(
        name="Anthropic",
        api_key="sk-test",
    )

    assert _is_own_machine_profile(openai) is False
    assert _is_own_machine_profile(anthropic) is False
