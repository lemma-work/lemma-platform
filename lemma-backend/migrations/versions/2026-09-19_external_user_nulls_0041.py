"""Make the external-user uniqueness apply to Telegram too, and reserve a number.

``ix_agent_surface_external_user_platform_tenant_external`` is unique over
(platform, tenant_id, external_user_id), and Telegram writes ``tenant_id``
NULL -- see ``platforms/telegram/parser.py``, where WhatsApp passes its
``waba_id`` and Telegram has nothing to pass. Postgres treats NULLs as distinct
by default, so for every Telegram sender the index has never applied.

That matters because the code relies on it. ``ExternalSurfaceUserRepository``
upserts inside a savepoint and retries on conflict, with a comment saying the
constraint is what resolves a concurrent insert; and the read is
``scalar_one_or_none``. Two inbound Telegram messages from one person racing --
a DM and a retried webhook delivery, which is exactly the case the savepoint
was written for -- leave two cache rows. Every later message from that person
then raises ``MultipleResultsFound``, which is not a ``DomainError`` and so
arrives as a 500, permanently, until somebody deletes a row by hand. The rows
can also disagree about ``resolved_user_id``, which makes identity resolution
answer differently depending on which one is read.

``NULLS NOT DISTINCT`` rather than backfilling ``tenant_id`` to ``''``: it
changes no data, and it says the thing that was meant -- one cache row per
sender per platform, whether or not that platform has a tenant.

Revision ID: 0041_external_user_nulls
Revises: 0040_one_surface_per_agent
"""

from alembic import op
import sqlalchemy as sa

revision = "0041_external_user_nulls"
down_revision = "0040_one_surface_per_agent"
branch_labels = None
depends_on = None

_INDEX = "ix_agent_surface_external_user_platform_tenant_external"
_COLUMNS = "platform, tenant_id, external_user_id"


def upgrade() -> None:
    # Duplicates can already exist on any deployment that has run Telegram, so
    # they are reported rather than met as a bare index-build failure. Which of
    # two cache rows to keep is a judgement about someone's identity, and not
    # one a migration should make: the rows may name different users.
    offenders = (
        op.get_bind()
        .execute(
            sa.text(
                f"SELECT platform, external_user_id, count(*) AS rows "
                f"FROM agent_surface_external_users "
                f"GROUP BY {_COLUMNS} HAVING count(*) > 1 ORDER BY rows DESC"
            )
        )
        .fetchall()
    )
    if offenders:
        listed = ", ".join(
            f"{row.platform} sender {row.external_user_id} has {row.rows} rows"
            for row in offenders[:10]
        )
        raise RuntimeError(
            "Cannot make external-user identity unique while duplicates exist: "
            f"{listed}"
            + (f" (and {len(offenders) - 10} more)" if len(offenders) > 10 else "")
            + ". Keep the row whose resolved_user_id is correct, delete the "
            "rest, then run this migration again."
        )
    op.drop_index(_INDEX, table_name="agent_surface_external_users")
    op.execute(
        f"CREATE UNIQUE INDEX {_INDEX} ON agent_surface_external_users "
        f"({_COLUMNS}) NULLS NOT DISTINCT"
    )

    # One agent per pooled WhatsApp number, the other half of 0040. That one
    # stops an agent holding two numbers; without this nothing stops two agents
    # holding one -- and with a pool the arriving number *is* the routing key,
    # so two claimants on one number is an inbound message with no answer to
    # "which agent". Partial and scoped to WhatsApp: a Slack or Teams bot id
    # may legitimately repeat across system-credential surfaces.
    op.execute(
        "CREATE UNIQUE INDEX uq_agent_pooled_whatsapp_number "
        "ON agent_surfaces (surface_identity_id) "
        "WHERE surface_type = 'WHATSAPP' AND surface_identity_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("uq_agent_pooled_whatsapp_number", table_name="agent_surfaces")
    op.drop_index(_INDEX, table_name="agent_surface_external_users")
    op.create_index(
        _INDEX,
        "agent_surface_external_users",
        ["platform", "tenant_id", "external_user_id"],
        unique=True,
    )
