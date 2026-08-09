"""The adapter contract: what shared code may call on any platform.

`SurfaceConfigurationMixin` and the ingress service reach for an adapter by
platform and call methods on whatever comes back. Nothing checked that every
adapter could answer those calls, so #303 added `parse_channel_setup` to Slack
alone and every Telegram and WhatsApp webhook raised `AttributeError` in the
worker — retried, dead-lettered, and the message reached nobody.

The rule these tests enforce: a method one adapter exposes publicly is part of
the contract and belongs on `BaseSurfaceAdapter`, with a default for platforms
that cannot do it. Anything genuinely private to one platform is underscored
and shared code cannot reach it.
"""

from __future__ import annotations

import inspect

import pytest

from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.platforms.base import BaseSurfaceAdapter

# Every adapter must implement these itself; the base deliberately declares no
# default, because a platform that cannot do them is not a surface at all.
REQUIRED_OF_EVERY_ADAPTER = (
    "parse_inbound_event",
    "send_message",
    "add_processing_indicator",
    "fetch_sender_profile",
)


def _registered_adapters() -> list[tuple[str, object]]:
    registry = SurfacePlatformAdapterRegistry()
    return [
        (platform, registry.get(platform)) for platform in registry.list_platforms()
    ]


def _public_methods(cls: type) -> set[str]:
    return {
        name
        for name, value in vars(cls).items()
        if not name.startswith("_") and callable(value)
    }


@pytest.mark.parametrize("platform, adapter", _registered_adapters())
def test_adapter_exposes_nothing_shared_code_cannot_call(platform, adapter):
    """A public adapter method must be on the base, so any platform answers it."""
    base_surface = {
        name for name, _ in inspect.getmembers(BaseSurfaceAdapter, callable)
    }
    undeclared = sorted(
        name
        for cls in type(adapter).__mro__
        if cls is not BaseSurfaceAdapter and issubclass(cls, BaseSurfaceAdapter)
        for name in _public_methods(cls)
        if name not in base_surface and name not in REQUIRED_OF_EVERY_ADAPTER
    )
    assert not undeclared, (
        f"{platform} exposes {undeclared} but BaseSurfaceAdapter does not declare "
        "them. Shared services call adapters by platform, so a method only one "
        "adapter has raises AttributeError on every other. Add it to the base "
        "with an inert default, or make it private to this adapter."
    )


@pytest.mark.parametrize("platform, adapter", _registered_adapters())
@pytest.mark.parametrize("method", REQUIRED_OF_EVERY_ADAPTER)
def test_adapter_implements_the_required_core(platform, adapter, method):
    assert callable(getattr(adapter, method, None)), (
        f"{platform} does not implement {method}(), which every surface needs."
    )


@pytest.mark.parametrize("platform, adapter", _registered_adapters())
async def test_channel_setup_is_answerable_by_every_platform(platform, adapter):
    """The specific regression: this is called on every inbound webhook."""
    assert await adapter.parse_channel_setup({}, {}) is None


@pytest.mark.parametrize("platform, adapter", _registered_adapters())
def test_adapter_overrides_accept_every_argument_the_base_declares(platform, adapter):
    """An override must take what the shared caller is entitled to pass.

    Names alone are not the contract. #303 added `metadata` to
    `stream_progress` on the base and on Slack, and the Telegram and Teams
    overrides kept the old signature — so every progress update on those two
    raised TypeError into a broad `except`, and live progress silently stopped
    working on both. Nothing failed; it just went quiet.
    """
    mismatches: list[str] = []
    for name, base_method in inspect.getmembers(
        BaseSurfaceAdapter, inspect.isfunction
    ):
        if name.startswith("_"):
            continue
        override = getattr(type(adapter), name, None)
        if override is None or override is base_method:
            continue
        base_params = inspect.signature(base_method).parameters
        override_params = inspect.signature(override).parameters
        if any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in override_params.values()
        ):
            continue
        missing = [
            param
            for param, spec in base_params.items()
            if spec.kind is inspect.Parameter.KEYWORD_ONLY
            and param not in override_params
        ]
        if missing:
            mismatches.append(f"{name}() is missing {missing}")
    assert not mismatches, (
        f"{platform} overrides drift from BaseSurfaceAdapter: {mismatches}. The "
        "shared caller passes what the base declares, so a narrower override "
        "raises TypeError at runtime — swallowed wherever the call is "
        "best-effort."
    )
