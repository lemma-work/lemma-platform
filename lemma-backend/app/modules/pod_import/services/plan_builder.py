"""Turn a bundle on disk into an ordered import plan.

This is the server-side counterpart of the CLI's plan: it walks the bundle in
dependency order, decides create-vs-update against what the pod already has, and
flags the table updates that would lose data — reusing ``lemma_pod_bundle`` so
the CLI and backend classify resources and destruction identically.

The "what already exists" question is a port (``ExistingResources``) so this
stays pure and unit-testable: tests pass a fake, production passes an adapter
over the pod's repositories.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from lemma_pod_bundle import (
    RESOURCE_KINDS,
    diff_table_columns,
    list_resource_names,
    order_tables_by_fk,
    read_manifest,
    read_requirements,
)

from app.modules.pod_import.domain.value_objects import (
    ImportAction,
    ImportStep,
)


class ExistingResources(Protocol):
    """What the target pod already contains — queried while planning."""

    def has(self, resource_type: str, name: str) -> bool: ...

    def table_schema(self, name: str) -> dict[str, Any] | None:
        """Current columns/primary key for a table, or None if it doesn't exist.
        Used to classify a table update as additive vs destructive."""
        ...


def _is_destructive_table_update(
    bundle_root: Path, name: str, existing: ExistingResources
) -> bool:
    current = existing.table_schema(name)
    if current is None:
        return False
    desired = read_manifest(bundle_root, "tables", name)
    return diff_table_columns(current, desired).is_destructive


def build_plan(
    bundle_root: Path, existing: ExistingResources
) -> tuple[list[ImportStep], dict[str, Any], list[dict[str, Any]]]:
    """Return ``(steps, requirements, capabilities)`` for the bundle.

    Steps are emitted in ``RESOURCE_KINDS`` dependency order; each is CREATE or
    UPDATE depending on whether the pod already has it, and table updates that
    drop or rebuild a column are marked ``destructive`` so the importer sees the
    data loss before applying.
    """
    bundle_root = Path(bundle_root)
    steps: list[ImportStep] = []

    for kind in RESOURCE_KINDS:
        names = list_resource_names(bundle_root, kind)
        if kind == "tables":
            # A table with a foreign key must be created after the table it
            # references, or the FK target won't exist yet.
            names = order_tables_by_fk(bundle_root, names)
        for name in names:
            exists = existing.has(kind, name)
            action = ImportAction.UPDATE if exists else ImportAction.CREATE
            destructive = (
                exists
                and kind == "tables"
                and _is_destructive_table_update(bundle_root, name, existing)
            )
            steps.append(
                ImportStep(
                    resource_type=kind,
                    resource_name=name,
                    action=action,
                    destructive=destructive,
                )
            )

    # Deferred grant pass: a grant can target a resource created later (an agent
    # granted a workflow, a peer agent, or a connector connected during consent),
    # so grants are replayed only after every resource exists. Emit one grant
    # step per agent/function that actually carries grants.
    for kind, grant_kind in (("agents", "agent_grants"), ("functions", "function_grants")):
        for name in list_resource_names(bundle_root, kind):
            manifest = read_manifest(bundle_root, kind, name)
            if (manifest.get("permissions") or {}).get("grants"):
                steps.append(
                    ImportStep(
                        resource_type=grant_kind,
                        resource_name=name,
                        action=ImportAction.CREATE,
                    )
                )

    summary = read_requirements(bundle_root)
    return steps, summary.get("requirements") or {}, summary.get("capabilities") or []
