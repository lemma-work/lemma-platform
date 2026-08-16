"""The delivery-marker statement has to be one PostgreSQL will accept.

``pod.delivered`` is claimed with a hand-written ``UPDATE`` against a JSONB
column, and the tests around it only ever exercised ``qualifies()`` -- the pure
predicate. Nothing ran the SQL, so a statement the server refuses to plan shipped
green through every gate and surfaced only when the feature was switched on in
development.

Two traps, and the statement hit both:

* ``jsonb_build_object`` declares its arguments ``"any"``, so PostgreSQL cannot
  infer a type for a bound parameter and asyncpg raises
  ``IndeterminateDatatypeError`` before the statement runs.
* Writing the cast as ``:now::text`` does not fix it, because SQLAlchemy's
  ``text()`` scans for ``:name`` and reads the second colon as a bind parameter
  called ``text``. It has to be ``CAST(:now AS text)``.

Failing silently was the worse half. The claim is guarded by
``NOT (... ? 'delivered_at')`` so it marks a pod once -- but a statement that
always raises never writes the marker, so the guard never became false and every
later outcome tried again. And since the raise escaped the consumer's handler,
the inbox retried the whole projection, re-emitting events that had already been
emitted before it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.composition.pod_delivery import DELIVERY_CLAIM_SQL, DeliveryVia
from app.modules.test_support.e2e import fixtures as e2e_fixtures

# Re-bound per file, the way the sibling outbox test does it: these are module
# attributes rather than a plugin, so each file that wants a database says so.
test_network = e2e_fixtures.test_network
postgres_container = e2e_fixtures.postgres_container
redis_container = e2e_fixtures.redis_container
supertokens_container = e2e_fixtures.supertokens_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
db_manager = e2e_fixtures.db_manager
db_session = e2e_fixtures.db_session
sandbox_reachable_backend = e2e_fixtures.sandbox_reachable_backend
worker = e2e_fixtures.worker
test_app = e2e_fixtures.test_app
async_client = e2e_fixtures.async_client
fixed_test_user = e2e_fixtures.fixed_test_user
authenticated_client = e2e_fixtures.authenticated_client
fixed_test_org = e2e_fixtures.fixed_test_org

pytestmark = [pytest.mark.e2e]


async def _create_pod(client, org_id: str) -> str:
    response = await client.post(
        "/pods",
        json={
            "name": f"delivery-{uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": org_id,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _params(pod_id, via: DeliveryVia) -> dict:
    return {
        "pod_id": pod_id,
        "now": datetime.now(timezone.utc).isoformat(),
        "via": via.value,
    }


async def test_the_delivery_claim_is_a_statement_postgres_can_plan(db_session) -> None:
    """The regression itself.

    No pod is needed: an ``UPDATE`` matching no row still has to be planned, and
    planning is exactly what used to fail.
    """
    await db_session.execute(
        text(DELIVERY_CLAIM_SQL), _params(uuid4(), DeliveryVia.AGENT_RUN)
    )


async def test_the_first_outcome_claims_activation_and_later_ones_do_not(
    db_session, authenticated_client, fixed_test_org
) -> None:
    """The guard is the whole design: many outcomes, one activation."""
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])

    first = await db_session.scalar(
        text(DELIVERY_CLAIM_SQL), _params(pod_id, DeliveryVia.SCHEDULE_RUN)
    )
    second = await db_session.scalar(
        text(DELIVERY_CLAIM_SQL), _params(pod_id, DeliveryVia.AGENT_RUN)
    )

    assert str(first) == pod_id, "the first qualifying outcome must win the claim"
    assert second is None, "a later outcome must not re-announce activation"

    stored = await db_session.scalar(
        text("SELECT config -> 'analytics' FROM pods WHERE id = :id"), {"id": pod_id}
    )
    assert stored["delivered_at"], "the timestamp is what makes the guard hold"
    assert stored["via"] == DeliveryVia.SCHEDULE_RUN.value, (
        "via records which outcome got there first, not the most recent one"
    )


async def test_claiming_preserves_whatever_else_the_pod_config_holds(
    db_session, authenticated_client, fixed_test_org
) -> None:
    """`config` is the pod's own settings blob, not analytics' scratch space."""
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    await db_session.execute(
        text("UPDATE pods SET config = CAST(:cfg AS jsonb) WHERE id = :id"),
        {"id": pod_id, "cfg": '{"theme": "dark"}'},
    )
    await db_session.commit()

    await db_session.execute(
        text(DELIVERY_CLAIM_SQL), _params(pod_id, DeliveryVia.SURFACE_MESSAGE)
    )

    config = await db_session.scalar(
        text("SELECT config FROM pods WHERE id = :id"), {"id": pod_id}
    )
    assert config["theme"] == "dark", "activation must not clobber pod settings"
    assert config["analytics"]["via"] == DeliveryVia.SURFACE_MESSAGE.value
