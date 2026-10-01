"""Exporting a pod's deciders into a bundle's ``deciders/`` directory.

One ``deciders/<name>/<name>.json`` per decider, holding its name and current
definition and nothing it has learned: its examples are people's answers about
their own data and its decisions are records of what it saw, so neither leaves
the pod (``lemma_pod_bundle.normalize._normalize_decider_payload`` keeps the two
keys that travel). The file is byte-for-byte what the CLI's exporter writes for
the same decider.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID

from lemma_pod_bundle.layout import _write_json
from lemma_pod_bundle.normalize import _normalize_decider_payload

from app.core.authorization.context import ResourceRef
from app.core.authorization.permissions import Permissions
from app.modules.pod_bundle.infrastructure.decider_apply import (
    DECIDERS_DIR,
    MOST_DECIDERS,
)


class MayAsk(Protocol):
    """The part of an authorization ``Context`` the export asks."""

    async def can(
        self, permission_id: str, resource: ResourceRef | None = None
    ) -> bool: ...


async def export_deciders(
    root: Path, *, pod_id: UUID, ctx: MayAsk, warnings: list[str]
) -> None:
    """Write the pod's deciders, if the person exporting may read them.

    The decisions contract does not authorize, so this asks what
    ``GET /pods/{pod_id}/deciders`` asks: ``decider.read`` on the pod. Someone
    without it gets a bundle with no deciders and a warning saying why, not a
    failed export.
    """
    from app.modules.decisions.contracts.deciders import list_deciders

    if not await ctx.can(Permissions.DECIDER_READ, ResourceRef.pod(pod_id)):
        warnings.append("deciders were left out: you may not read this pod's deciders.")
        return
    found = await list_deciders(pod_id=pod_id, limit=MOST_DECIDERS)
    if len(found) >= MOST_DECIDERS:
        warnings.append(
            f"only the first {MOST_DECIDERS} deciders, by name, were exported."
        )
    for decider in found:
        payload = _normalize_decider_payload(
            {
                "name": decider.name,
                "definition": decider.definition.model_dump(mode="json"),
            }
        )
        dir_ = root / DECIDERS_DIR / decider.name
        dir_.mkdir(parents=True, exist_ok=True)
        _write_json(dir_ / f"{decider.name}.json", payload)
