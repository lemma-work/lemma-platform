"""API boot must not grow with the number of pods.

A grant backfill over every pod schema ran on every API start. It was cheap
when written and grew with every pod ever created -- schemas were never dropped
-- until it was most of the boot, and a boot that drifts past the liveness
deadline restarts into the same boot.

This boots every module's API lifespan against a datastore holding a thousand
pod schemas and holds each step to a budget. The budgets are far above
what a hook that ignores the data costs, and far below what the old backfill
cost at this size, so the test separates the two without depending on the
machine's speed. `test_boot_hooks.py` is the other half: no hook gets onto the
boot path without saying why it does not scale.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.registry.assembly import enter_api_lifespans
from app.core.registry.installed import OSS_MODULES
from app.modules.datastore.infrastructure.session import get_datastore_engine

pytestmark = pytest.mark.e2e

#: Enough that a per-schema cost dominates any fixed one. Each has a table, as a
#: real pod's does, because a grant over a schema's tables costs per table.
#: Measured locally, the old backfill took about three times the step budget
#: at this size, and every current hook well under a hundredth of it.
POD_SCHEMAS = 1000
STEP_BUDGET_MS = 100.0
MODULE_PHASE_BUDGET_MS = 250.0


def _schema_names() -> list[str]:
    return [f"pod_{str(uuid4()).replace('-', '_')}" for _ in range(POD_SCHEMAS)]


async def _create(names: list[str]) -> None:
    async with get_datastore_engine().begin() as conn:
        for name in names:
            await conn.execute(text(f'CREATE SCHEMA "{name}"'))
            await conn.execute(
                text(f'CREATE TABLE "{name}".items (id int PRIMARY KEY)')
            )


async def _drop(names: list[str]) -> None:
    async with get_datastore_engine().begin() as conn:
        for name in names:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))


def _module_steps(caplog) -> dict[str, float]:
    module_names = {module.name for module in OSS_MODULES}
    return {
        record.msg["step"]: record.msg["duration_ms"]
        for record in caplog.records
        if isinstance(record.msg, dict)
        and record.msg.get("event") == "service.startup.step"
        and record.msg["step"].split(".", 1)[0] in module_names
    }


async def _boot_module_lifespans(app) -> None:
    async with AsyncExitStack() as stack:
        await enter_api_lifespans(stack, OSS_MODULES, app)


async def test_module_boot_does_not_scale_with_pod_schemas(test_app, caplog):
    names = _schema_names()
    await _create(names)
    try:
        # Once to pay for first imports, which are not what this measures.
        await _boot_module_lifespans(test_app)
        caplog.clear()
        caplog.set_level(logging.INFO)

        await _boot_module_lifespans(test_app)
    finally:
        await _drop(names)

    steps = _module_steps(caplog)
    assert steps, "no module startup step was logged"
    slow = {step: ms for step, ms in steps.items() if ms >= STEP_BUDGET_MS}
    assert not slow, (
        f"module boot steps over {STEP_BUDGET_MS}ms with {POD_SCHEMAS} pod "
        f"schemas: {slow}. A boot hook must not scale with data; backfills go "
        "in a migration or a worker job."
    )
    total = sum(steps.values())
    assert total < MODULE_PHASE_BUDGET_MS, (
        f"module boot phase took {total:.0f}ms: {steps}"
    )
