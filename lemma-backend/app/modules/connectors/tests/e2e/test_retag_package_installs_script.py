"""scripts/retag_package_installs.py against a real Postgres.

The unique index on (connector_id, kind, event_type) is what made the original
script roll back entirely when a package trigger had an http twin; only a real
database enforces it, so only a real database proves the fix.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.modules.connectors.infrastructure.models.connector_trigger import (
    ConnectorTrigger,
)

pytestmark = pytest.mark.e2e

_SCRIPT = Path(__file__).resolve().parents[5] / "scripts" / "retag_package_installs.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("retag_package_installs", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _insert_trigger(db_session, connector_id: str, kind: str, event: str) -> str:
    trigger_id = f"{kind}-{event}-{uuid4().hex[:8]}"
    await db_session.execute(
        text(
            "INSERT INTO connector_triggers (id, connector_id, kind, event_type, created_at, updated_at) "
            "VALUES (:id, :c, :k, :e, now(), now())"
        ),
        {"id": trigger_id, "c": connector_id, "k": kind, "e": event},
    )
    return trigger_id


async def test_package_duplicates_fold_into_their_http_twin(
    db_session, connector_test_connector, fixed_test_user
):
    script = _load_script()
    connector_id = connector_test_connector.id

    lone = await _insert_trigger(db_session, connector_id, "package", "lone")
    unbound_pkg = await _insert_trigger(db_session, connector_id, "package", "unbound")
    unbound_http = await _insert_trigger(db_session, connector_id, "http", "unbound")
    bound_pkg = await _insert_trigger(db_session, connector_id, "package", "bound")
    bound_http = await _insert_trigger(db_session, connector_id, "http", "bound")
    schedule_id = uuid4()
    await db_session.execute(
        text(
            "INSERT INTO schedules (id, user_id, schedule_type, connector_trigger_id, "
            "config, visibility, is_active, is_internal, consecutive_failures, "
            "created_at, updated_at) "
            "VALUES (:id, :u, 'WEBHOOK', :t, '{}'::jsonb, 'POD', true, false, 0, now(), now())"
        ),
        {"id": schedule_id, "u": fixed_test_user["id"], "t": bound_pkg},
    )
    await db_session.commit()

    touched = await script.retag_in_session(db_session)
    await db_session.commit()

    assert touched["deduplicated"] >= 2
    assert touched["repointed"] >= 1
    kinds = dict(
        (
            await db_session.execute(
                text("SELECT id, kind FROM connector_triggers WHERE connector_id = :c"),
                {"c": connector_id},
            )
        ).all()
    )
    assert kinds == {lone: "http", unbound_http: "http", bound_http: "http"}
    assert unbound_pkg not in kinds and bound_pkg not in kinds
    bound_to = (
        await db_session.execute(
            text("SELECT connector_trigger_id FROM schedules WHERE id = :id"),
            {"id": schedule_id},
        )
    ).scalar_one()
    assert bound_to == bound_http

    rerun = await script.retag_in_session(db_session)
    await db_session.commit()
    assert rerun == dict.fromkeys(rerun, 0)

    # Rows are readable by the ORM again.
    assert await db_session.get(ConnectorTrigger, lone) is not None
