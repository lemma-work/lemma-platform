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
    for name, base_method in inspect.getmembers(BaseSurfaceAdapter, inspect.isfunction):
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


def _overrides(adapter: object, name: str) -> bool:
    """Does this adapter supply ``name`` itself, rather than inheriting the base?"""
    return any(
        name in vars(cls)
        for cls in type(adapter).__mro__
        if cls is not BaseSurfaceAdapter and issubclass(cls, BaseSurfaceAdapter)
    )


@pytest.mark.parametrize("platform, adapter", _registered_adapters())
def test_a_platform_that_parses_interactions_also_acknowledges_them(platform, adapter):
    """The two halves of an interaction are one capability, not two.

    `acknowledge_interaction` had a silent no-op default and only Telegram
    overrode it, so on Slack, Teams and WhatsApp a tapped Approve produced no
    confirmation, left the buttons live, and — because `handle_interaction`
    routes every failure through this same call — reported a failed submission
    to nobody at all while the run stayed WAITING.

    A platform that cannot receive interactions is free to implement neither.
    Implementing only the inbound half is what is banned.
    """
    if not _overrides(adapter, "parse_inbound_interaction"):
        pytest.skip(f"{platform} receives no interactions")
    assert _overrides(adapter, "acknowledge_interaction"), (
        f"{platform} parses interactions but inherits the no-op "
        "acknowledge_interaction(), so a person tapping a button is told nothing."
    )


# Outbound conversation content goes through `deliver` and nowhere else. These
# two are the port's remaining public sends, and both are deliberate:
#
#   send_message   — the text primitive `deliver` itself degrades onto, and the
#                    only way to say something before a conversation exists
#                    (the signup and setup replies in fallback_reply_service).
#   send_cold_email — not a reply at all. It opens a thread rather than landing
#                    in one, which is the one thing email can do that chat
#                    cannot, and it has no envelope to belong to.
_PUBLIC_SENDS_THAT_ARE_NOT_CONTENT = {"send_message", "send_cold_email"}

# Setup and platform chrome. Not conversation delivery, and slated to move to
# their own optional protocol; listed so this test fails when a *new* one
# appears rather than when the known ones do.
_CHROME = {
    "send_starter_prompt",
    "send_channel_setup_prompt",
    "publish_home_view",
    "open_channel_setup_modal",
    "open_dm_agent_modal",
    "set_thread_title",
}


def test_the_port_exposes_no_public_verb_for_a_kind_of_content():
    """One seam for content, so a new kind cannot add a hole per platform.

    Each of `send_questions`, `send_approval`, `send_voice_note`,
    `send_file_attachment` and `send_display_resource` was a public verb every
    platform had to answer and most answered with a default `return False` —
    indistinguishable from a platform that genuinely declined. They are
    `_render_*` hooks now, reachable only from `deliver`, so "does this
    platform support choices" is a branch inside one method that always exists
    rather than a method that might not.
    """
    public_sends = {
        name
        for name, _ in inspect.getmembers(BaseSurfaceAdapter, callable)
        if name.startswith(("send_", "publish_", "open_", "set_thread"))
    }
    unexpected = sorted(
        public_sends - _PUBLIC_SENDS_THAT_ARE_NOT_CONTENT - _CHROME
    )
    assert not unexpected, (
        f"{unexpected} are public outbound verbs. A kind of content belongs in "
        "SurfaceEnvelope with a `_render_*` hook, not in a verb every platform "
        "has to remember to implement."
    )


def test_nothing_outside_the_delivery_mixin_renders_content_itself():
    """The seam is only a seam while everything goes through it.

    A static check rather than a typed one, because Python cannot express
    "protected" — and the failure this prevents is precisely someone reaching
    past `deliver` for one platform's native render and quietly reintroducing
    the ladder that used to be copied at every call site.
    """
    from pathlib import Path

    module_root = Path(__file__).resolve().parents[2]
    assert module_root.name == "agent_surfaces", module_root
    allowed = {"envelope_delivery.py", "base.py"}
    scanned = 0
    offenders: list[str] = []
    for path in module_root.rglob("*.py"):
        if path.name in allowed or "/tests/" in path.as_posix():
            continue
        source = path.read_text()
        scanned += 1
        for hook in (
            "_render_choices",
            "_render_decision",
            "_render_file",
            "_render_voice",
            "_render_resource",
        ):
            # A definition is a platform implementing its half. A call is the
            # thing being banned.
            if f".{hook}(" in source and f"def {hook}(" not in source:
                offenders.append(f"{path.name}:{hook}")
    assert scanned > 50, f"the scan found only {scanned} files; it is not looking"
    assert not offenders, (
        f"{offenders} call a render hook directly. Build a SurfaceEnvelope and "
        "call deliver() so the part degrades the same way it does everywhere else."
    )
