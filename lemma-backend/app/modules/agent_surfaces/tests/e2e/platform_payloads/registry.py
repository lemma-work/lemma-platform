"""The shape of a pinned payload, and where the contract test finds them all."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.modules.agent_surfaces.domain.entities import SurfacePlatform


@dataclass(frozen=True, slots=True)
class InboundCase:
    """One inbound payload, and what the production parser must make of it.

    ``expected`` names fields on ``ParsedInboundSurfaceEvent`` and the values
    they must hold. It is deliberately not the whole model: a case pins what
    the payload is *for* — that a DM is a DM, that a mention is attributed to
    the right sender, that an attachment survives — and leaves the rest free so
    an unrelated field gaining a default does not break every case at once.
    """

    name: str
    platform: SurfacePlatform
    payload: dict[str, Any]
    expected: dict[str, Any]
    #: Set when the parser is meant to refuse this payload, and say why.
    refused: bool = False


@dataclass(frozen=True, slots=True)
class InteractionCase:
    """One native submission, and what ``parse_interaction`` must make of it."""

    name: str
    platform: SurfacePlatform
    payload: dict[str, Any]
    expected: dict[str, Any] = field(default_factory=dict)
    refused: bool = False


#: Populated by each platform module at import. The contract test iterates
#: these, so a builder that is not registered is a builder nothing checks --
#: which is the state this module exists to end.
INBOUND_BUILDERS: dict[str, Callable[[], list[InboundCase]]] = {}
INTERACTION_BUILDERS: dict[str, Callable[[], list[InteractionCase]]] = {}


def register_inbound(
    platform: SurfacePlatform,
) -> Callable[[Callable[[], list[InboundCase]]], Callable[[], list[InboundCase]]]:
    def decorate(
        builder: Callable[[], list[InboundCase]],
    ) -> Callable[[], list[InboundCase]]:
        INBOUND_BUILDERS[platform.value] = builder
        return builder

    return decorate


def register_interactions(
    platform: SurfacePlatform,
) -> Callable[
    [Callable[[], list[InteractionCase]]], Callable[[], list[InteractionCase]]
]:
    def decorate(
        builder: Callable[[], list[InteractionCase]],
    ) -> Callable[[], list[InteractionCase]]:
        INTERACTION_BUILDERS[platform.value] = builder
        return builder

    return decorate
