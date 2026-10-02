"""Defining, listing and testing pod deciders from another module.

The agent tools use these. They do not authorize: the caller checks the
`decider.*` permission on its own authorization context first, the way every
agent tool gets its authority from `agent/tools/authority.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.modules.decisions.domain.deciders import DeciderDefinition, DeciderEntity
from app.modules.decisions.domain.errors import (
    DeciderExistsError,
    DeciderInvalidError,
    DeciderNotFoundError,
)
from app.modules.decisions.services.deciders_service import (
    SampleDisagreement,
    SampleResult,
    SampleRow,
    warnings_for,
)
from app.modules.decisions.services.decisions_service import Asker
from app.modules.decisions.services.system_deciders import SYSTEM_DECIDERS
from app.modules.decisions.services.wiring import build_deciders_service


async def get_decider(*, pod_id: UUID, name: str) -> DeciderEntity | None:
    try:
        return await build_deciders_service().get(pod_id=pod_id, name=name)
    except DeciderNotFoundError:
        return None


async def list_deciders(*, pod_id: UUID, limit: int = 100) -> list[DeciderEntity]:
    return await build_deciders_service().list(pod_id=pod_id, limit=limit)


async def define_decider(
    *,
    pod_id: UUID,
    user_id: UUID,
    name: str,
    definition: DeciderDefinition,
) -> tuple[DeciderEntity, list[str]]:
    """Create the decider, or save a new version when the name exists.

    Returns the saved decider and the warnings its definition earns.
    """
    service = build_deciders_service()
    try:
        saved = await service.create(
            pod_id=pod_id, user_id=user_id, name=name, definition=definition
        )
    except DeciderExistsError:
        saved = await service.update(
            pod_id=pod_id, user_id=user_id, name=name, definition=definition
        )
    return saved, warnings_for(definition)


async def test_decider(
    *,
    rows: Sequence[SampleRow],
    asker: Asker,
    decider: str | None = None,
    definition: DeciderDefinition | None = None,
) -> SampleResult:
    """Decide sample rows without recording, compared with expected answers."""
    return await build_deciders_service().test(
        decider=decider, definition=definition, rows=rows, asker=asker
    )


def system_decider_names() -> list[str]:
    return [f"system:{name}" for name in SYSTEM_DECIDERS]


__all__ = [
    "DeciderEntity",
    "DeciderExistsError",
    "DeciderInvalidError",
    "DeciderNotFoundError",
    "SampleDisagreement",
    "SampleResult",
    "SampleRow",
    "define_decider",
    "get_decider",
    "list_deciders",
    "system_decider_names",
    "test_decider",
]
