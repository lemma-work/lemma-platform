from __future__ import annotations

from uuid import uuid4

from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
)
from app.modules.agent.domain.value_objects import HarnessKind
from app.modules.agent.events.handlers import build_harness_registry
from app.modules.agent.services.runtime_profile_service import USER_DAEMON_PROFILE_PROTOCOLS


def test_gg_coder_runtime_profile_is_supported_end_to_end():
    profile = AgentRuntimeProfile(
        id="gg-coder",
        organization_id=uuid4(),
        user_id=uuid4(),
        daemon_id=uuid4(),
        scope=RuntimeProfileScope.PERSONAL,
        kind=RuntimeProfileKind.HARNESS,
        protocol=RuntimeProfileProtocol.GG_CODER,
        name="GG Coder",
    )

    assert HarnessKind("ggcoder") is HarnessKind.GG_CODER
    assert profile.derived_harness_kind() is HarnessKind.GG_CODER
    assert USER_DAEMON_PROFILE_PROTOCOLS[HarnessKind.GG_CODER] is RuntimeProfileProtocol.GG_CODER
    assert build_harness_registry().get(HarnessKind.GG_CODER).kind is HarnessKind.GG_CODER
