"""The MCP bridges have to derive a vision mode that is actually true.

`test_vision_modes.py` asserts that `vision_mode_from_runtime_profile` reads
capabilities out of a snapshot -- and it does. What it never checked is whether
the bridge passes it a snapshot that has any. It did not: `run.agent_runtime` is
an `AgentRuntimeConfig`, a profile id and a model name, with no
`model_capabilities` key at all. So the helper was fed an object that could only
ever answer "no", every remote harness looked text-only, and a Claude Code or
Codex host that reads images natively was told PDF pages could not be viewed.

These cover the derivation itself, which is the part that was wrong.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.modules.agent.domain.vision import (
    AgentVisionMode,
    vision_mode_from_runtime_profile,
)


def test_the_stored_run_config_alone_can_never_report_vision():
    """The shape the bridge used to read from, asserted directly.

    This is the whole bug in one line: whatever the harness model is, a config
    with no capabilities resolves to "cannot see".
    """
    from app.core.domain.runtime import AgentRuntimeConfig

    stored = AgentRuntimeConfig(profile_id="p", model_name="claude-opus-5").model_dump(
        mode="json"
    )
    assert "model_capabilities" not in stored
    assert vision_mode_from_runtime_profile(stored) is not AgentVisionMode.DIRECT


@pytest.mark.asyncio
async def test_the_bridge_resolves_the_runtime_so_capabilities_are_present(monkeypatch):
    """A resolved runtime carries capabilities; the bridge must use that."""
    from app.modules.agent.services import conversation_mcp_service as bridge

    resolved = SimpleNamespace(
        public_snapshot=lambda: {
            "profile_id": "p",
            "model_name": "claude-opus-5",
            "model_capabilities": ["TEXT", "TOOLS", "VISION"],
        }
    )

    class _Service:
        def __init__(self, *a, **k):
            pass

        resolve = AsyncMock(return_value=resolved)

    monkeypatch.setattr(bridge, "AgentRuntimeProfileService", _Service)
    monkeypatch.setattr(
        bridge, "AgentRuntimeProfileRepository", lambda *a, **k: object()
    )
    monkeypatch.setattr(bridge, "AgentHostRepository", lambda *a, **k: object())
    monkeypatch.setattr(bridge, "get_secret_cipher", object)

    service = bridge.ConversationMCPService.__new__(bridge.ConversationMCPService)
    run = SimpleNamespace(
        agent_runtime=SimpleNamespace(
            model_dump=lambda mode="json": {"profile_id": "p", "model_name": "m"}
        )
    )

    profile = await service._resolved_runtime_profile(
        run=run, uow=object(), organization_id=uuid4(), user_id=uuid4()
    )

    assert profile["model_capabilities"] == ["TEXT", "TOOLS", "VISION"]
    assert vision_mode_from_runtime_profile(profile) is AgentVisionMode.DIRECT


@pytest.mark.asyncio
async def test_a_failed_resolve_falls_back_instead_of_failing_the_tool_call(
    monkeypatch,
):
    """A profile archived mid-run must not turn every tool call into an error.

    Falling back to the stored config restores exactly the previous behaviour --
    delegate if a VISION_MODEL exists, otherwise refuse images -- which is a
    worse answer but a working one.
    """
    from app.modules.agent.services import conversation_mcp_service as bridge

    class _Service:
        def __init__(self, *a, **k):
            pass

        resolve = AsyncMock(side_effect=RuntimeError("profile archived"))

    monkeypatch.setattr(bridge, "AgentRuntimeProfileService", _Service)
    monkeypatch.setattr(
        bridge, "AgentRuntimeProfileRepository", lambda *a, **k: object()
    )
    monkeypatch.setattr(bridge, "AgentHostRepository", lambda *a, **k: object())
    monkeypatch.setattr(bridge, "get_secret_cipher", object)

    service = bridge.ConversationMCPService.__new__(bridge.ConversationMCPService)
    run = SimpleNamespace(
        agent_runtime=SimpleNamespace(
            model_dump=lambda mode="json": {"profile_id": "p", "model_name": "m"}
        )
    )

    profile = await service._resolved_runtime_profile(
        run=run, uow=object(), organization_id=uuid4(), user_id=uuid4()
    )

    assert profile == {"profile_id": "p", "model_name": "m"}


@pytest.mark.asyncio
async def test_a_run_less_conversation_has_no_runtime_to_resolve(monkeypatch):
    from app.modules.agent.services import conversation_mcp_service as bridge

    service = bridge.ConversationMCPService.__new__(bridge.ConversationMCPService)
    assert (
        await service._resolved_runtime_profile(
            run=None, uow=object(), organization_id=uuid4(), user_id=uuid4()
        )
        is None
    )


def test_the_pod_bridge_lets_its_client_decide_about_images():
    """The pod MCP bridge has no run, so it cannot resolve anything -- but MCP
    carries images natively, so producing them and letting the client choose
    beats refusing on its behalf."""
    import inspect

    from app.modules.agent.services import pod_mcp_service

    source = inspect.getsource(pod_mcp_service.PodMCPService._context_from_token)
    assert "vision_mode=AgentVisionMode.DIRECT" in source


@pytest.mark.asyncio
async def test_a_harness_that_learned_to_see_is_believed_over_its_stored_catalog():
    """A harness registers before its ACP probe lands, so `images` is false then.

    The profile's catalog is copied from that first answer and only rebuilt when
    somebody edits the profile in Models settings. Until then a Claude Code or
    Codex host that reads images natively is described as text-only -- which is
    the second half of why vision was unavailable, and would have made the
    bridge fix above inert for exactly the people who hit the bug.
    """
    from app.modules.agent.domain.runtime_profiles import (
        RuntimeModelCapability,
        RuntimeModelCatalogEntry,
        RuntimeProfileKind,
    )
    from app.modules.agent.services.runtime_profile_service import (
        AgentRuntimeProfileService,
        with_harness_vision,
    )

    harness_id = uuid4()
    stale = RuntimeModelCatalogEntry(
        name="claude-opus-5",
        display_name="Claude Opus 5",
        provider_model_name="claude-opus-5",
        capabilities=[RuntimeModelCapability.TEXT, RuntimeModelCapability.TOOLS],
    )
    profile = SimpleNamespace(kind=RuntimeProfileKind.HARNESS, harness_id=harness_id)

    class _Hosts:
        async def get_harnesses(self, ids):
            return {harness_id: SimpleNamespace(capabilities={"images": True})}

    service = AgentRuntimeProfileService(None, _Hosts())
    refreshed = with_harness_vision(
        stale, harness_sees=await service._harness_reads_images(profile)
    )

    assert RuntimeModelCapability.VISION in refreshed.capabilities
    # Additive only -- the stored entry is not mutated.
    assert RuntimeModelCapability.VISION not in stale.capabilities


@pytest.mark.asyncio
async def test_a_harness_that_cannot_see_leaves_the_stored_catalog_alone():
    """Only ever additive: an operator may have edited the catalog deliberately."""
    from app.modules.agent.domain.runtime_profiles import (
        RuntimeModelCapability,
        RuntimeModelCatalogEntry,
        RuntimeProfileKind,
    )
    from app.modules.agent.services.runtime_profile_service import (
        AgentRuntimeProfileService,
        with_harness_vision,
    )

    harness_id = uuid4()
    stored = RuntimeModelCatalogEntry(
        name="m",
        display_name="m",
        provider_model_name="m",
        capabilities=[RuntimeModelCapability.TEXT],
    )
    profile = SimpleNamespace(kind=RuntimeProfileKind.HARNESS, harness_id=harness_id)

    class _Hosts:
        async def get_harnesses(self, ids):
            return {harness_id: SimpleNamespace(capabilities={"images": False})}

    service = AgentRuntimeProfileService(None, _Hosts())
    result = with_harness_vision(
        stored, harness_sees=await service._harness_reads_images(profile)
    )
    assert result.capabilities == [RuntimeModelCapability.TEXT]


@pytest.mark.asyncio
async def test_a_harness_lookup_failure_never_fails_the_run():
    """A capability hint is not worth losing a run over."""
    from app.modules.agent.domain.runtime_profiles import (
        RuntimeModelCapability,
        RuntimeModelCatalogEntry,
        RuntimeProfileKind,
    )
    from app.modules.agent.services.runtime_profile_service import (
        AgentRuntimeProfileService,
        with_harness_vision,
    )

    stored = RuntimeModelCatalogEntry(
        name="m",
        display_name="m",
        provider_model_name="m",
        capabilities=[RuntimeModelCapability.TEXT],
    )
    profile = SimpleNamespace(kind=RuntimeProfileKind.HARNESS, harness_id=uuid4())

    class _Hosts:
        async def get_harnesses(self, ids):
            # What a failed harness lookup actually raises now that the catch
            # names its exception instead of swallowing everything.
            raise SQLAlchemyError("host database is down")

    service = AgentRuntimeProfileService(None, _Hosts())
    result = with_harness_vision(
        stored, harness_sees=await service._harness_reads_images(profile)
    )
    assert result is stored


def _entry(name: str, *capabilities):
    from app.modules.agent.domain.runtime_profiles import RuntimeModelCatalogEntry

    return RuntimeModelCatalogEntry(
        name=name,
        display_name=name,
        provider_model_name=name,
        capabilities=list(capabilities),
    )


def test_a_harness_with_no_model_pinned_still_reports_what_it_can_do():
    """The shape every Agent Host run actually has, and the one that was broken.

    A run stores `{"profile_id": ...}` and nothing else, and an Agent Host
    profile routinely has no `default_model_name` -- `agent_host_model_catalog`
    documents an unpinned profile as meaning "let the harness use its own
    default". So `_selected_model` returns None, and capabilities were read off
    that None as `[]`: every such runtime was reported unable to see, however
    loudly its catalog and its harness said otherwise.
    """
    from app.modules.agent.domain.runtime_profiles import (
        RuntimeModelCapability,
        RuntimeProfileKind,
    )
    from app.modules.agent.services.runtime_profile_service import (
        unselected_capabilities,
    )

    profile = SimpleNamespace(
        kind=RuntimeProfileKind.HARNESS,
        model_catalog=[
            _entry(
                "default",
                RuntimeModelCapability.TEXT,
                RuntimeModelCapability.TOOLS,
                RuntimeModelCapability.VISION,
            ),
            _entry(
                "opus[1m]",
                RuntimeModelCapability.TEXT,
                RuntimeModelCapability.TOOLS,
                RuntimeModelCapability.VISION,
            ),
        ],
    )

    capabilities = unselected_capabilities(profile, harness_sees=True)

    assert RuntimeModelCapability.VISION in capabilities


def test_an_unselected_catalog_reports_only_what_every_model_shares():
    """Intersection, not the first entry.

    Nothing selected means any of them could run, so claiming a capability only
    some of them have would hand images to a model that cannot read them -- the
    exact failure the vision mode exists to prevent, arrived at from the other
    direction.
    """
    from app.modules.agent.domain.runtime_profiles import (
        RuntimeModelCapability,
        RuntimeProfileKind,
    )
    from app.modules.agent.services.runtime_profile_service import (
        unselected_capabilities,
    )

    profile = SimpleNamespace(
        kind=RuntimeProfileKind.HARNESS,
        model_catalog=[
            _entry(
                "sees",
                RuntimeModelCapability.TEXT,
                RuntimeModelCapability.VISION,
            ),
            _entry("blind", RuntimeModelCapability.TEXT),
        ],
    )

    capabilities = unselected_capabilities(profile, harness_sees=False)

    assert RuntimeModelCapability.VISION not in capabilities
    assert RuntimeModelCapability.TEXT in capabilities


def test_an_empty_catalog_falls_back_to_the_harness_itself():
    """A harness offering no `model` option yields an empty catalog by design."""
    from app.modules.agent.domain.runtime_profiles import (
        RuntimeModelCapability,
        RuntimeProfileKind,
    )
    from app.modules.agent.services.runtime_profile_service import (
        unselected_capabilities,
    )

    profile = SimpleNamespace(kind=RuntimeProfileKind.HARNESS, model_catalog=[])

    assert RuntimeModelCapability.VISION in unselected_capabilities(
        profile, harness_sees=True
    )
    assert RuntimeModelCapability.VISION not in unselected_capabilities(
        profile, harness_sees=False
    )


def test_the_snapshot_the_bridge_reads_carries_those_capabilities():
    """The whole point: this dict is what `vision_mode_from_runtime_profile` sees.

    Deriving the capabilities is worthless if `public_snapshot` still reports
    `[]` whenever no model is selected, which is what it did.
    """
    from app.modules.agent.domain.runtime_profiles import (
        RuntimeModelCapability,
        RuntimeProfileKind,
    )
    from app.modules.agent.services.runtime_profile_service import (
        ResolvedAgentRuntime,
    )
    from app.modules.agent.domain.value_objects import HarnessOptions  # noqa: F401

    profile = SimpleNamespace(
        id="p",
        name="Claude Code",
        user_id=None,
        harness_id=None,
        scope=SimpleNamespace(value="PERSONAL"),
        protocol=SimpleNamespace(value="AGENT_HOST"),
        kind=RuntimeProfileKind.HARNESS,
        config=None,
    )
    resolved = ResolvedAgentRuntime(
        profile=profile,
        harness_kind=SimpleNamespace(),
        model=None,
        provider_model_name=None,
        credentials=None,
        unselected_capabilities=[
            RuntimeModelCapability.TEXT,
            RuntimeModelCapability.VISION,
        ],
    )

    snapshot = resolved.public_snapshot()

    assert "VISION" in snapshot["model_capabilities"]
    assert vision_mode_from_runtime_profile(snapshot) is AgentVisionMode.DIRECT
    # Still unpinned: naming a model here would tell the harness what to run.
    assert snapshot["model_name"] is None
