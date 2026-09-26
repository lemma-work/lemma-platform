"""The organization's chosen model: which profile carries the mark, and what it
resolves to.

An organization has no row of its own to hold "the model teammates run on", and
a new table for one pointer would be a second place a profile id can go stale.
So the choice lives on the profile it names, under one metadata key. That keeps
the invariants local: archiving the profile drops the mark in the same write,
and a profile that is gone takes its mark with it.

Only an organization-wide model provider qualifies. A personal key is one
member's credential, and a coding agent runs on one member's computer; neither
may become what every teammate in a shared pod runs on because an owner clicked
"Make default".
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeProfileKind,
    RuntimeProfileScope,
    RuntimeProfileStatus,
)
from app.modules.agent.domain.value_objects import AgentRuntimeConfig

ORGANIZATION_DEFAULT_KEY = "organization_default"

# The user a listing is scoped to when only organization-wide profiles are
# wanted: nobody's personal profile belongs to the nil id, so what comes back is
# exactly what every member of the organization can see.
ORGANIZATION_WIDE_VIEWER = UUID(int=0)


def can_be_organization_default(profile: AgentRuntimeProfile) -> bool:
    """Whether ``profile`` may carry the organization's default mark."""
    return (
        profile.kind is RuntimeProfileKind.MODEL_PROVIDER
        and profile.scope is RuntimeProfileScope.ORGANIZATION
        and profile.status is RuntimeProfileStatus.ACTIVE
    )


def is_marked_default(profile: AgentRuntimeProfile) -> bool:
    """Whether the mark is stored on ``profile``, eligible or not."""
    return isinstance(profile.metadata.get(ORGANIZATION_DEFAULT_KEY), dict)


def organization_default_of(
    profiles: list[AgentRuntimeProfile],
) -> AgentRuntimeConfig | None:
    """The runtime the organization's mark points at, or ``None``.

    A mark on a profile that can no longer be the default -- archived by a path
    that did not clear it, say -- is ignored rather than trusted. The stored
    model is honoured only while the provider still lists it: providers retire
    models, and "the provider's own default" is the choice an owner would make
    again rather than a run failing on a name that no longer exists.
    """
    for profile in profiles:
        if not is_marked_default(profile) or not can_be_organization_default(profile):
            continue
        mark = profile.metadata[ORGANIZATION_DEFAULT_KEY]
        stored = mark.get("model_name")
        names = {entry.name for entry in profile.model_catalog}
        model_name = (
            stored
            if isinstance(stored, str) and stored in names
            else profile.default_model_name
        )
        return AgentRuntimeConfig(profile_id=profile.id, model_name=model_name)
    return None


def with_default_mark(
    profile: AgentRuntimeProfile, *, model_name: str | None
) -> AgentRuntimeProfile:
    """``profile`` carrying the mark, on ``model_name`` or its own default.

    Raises ``ValueError`` for a profile that may not be the default, or a model
    the provider does not list -- both are caller mistakes worth a 400.
    """
    if not can_be_organization_default(profile):
        raise ValueError(
            "Only an active, organization-wide model provider can be the "
            "organization's default"
        )
    if model_name is not None and model_name not in {
        entry.name for entry in profile.model_catalog
    }:
        raise ValueError(f"{profile.name} does not offer the model {model_name!r}")
    return profile.with_changes(
        metadata={
            **profile.metadata,
            ORGANIZATION_DEFAULT_KEY: {"model_name": model_name},
        }
    )


def without_default_mark(profile: AgentRuntimeProfile) -> AgentRuntimeProfile:
    """``profile`` with the mark removed; the same object when it had none."""
    if ORGANIZATION_DEFAULT_KEY not in profile.metadata:
        return profile
    metadata = {
        key: value
        for key, value in profile.metadata.items()
        if key != ORGANIZATION_DEFAULT_KEY
    }
    return profile.with_changes(metadata=metadata)


__all__ = [
    "ORGANIZATION_DEFAULT_KEY",
    "ORGANIZATION_WIDE_VIEWER",
    "can_be_organization_default",
    "is_marked_default",
    "organization_default_of",
    "with_default_mark",
    "without_default_mark",
]
