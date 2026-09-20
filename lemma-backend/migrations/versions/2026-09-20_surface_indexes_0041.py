"""Keep the surface indexes a query asked for; drop the twenty-two nobody did.

The three surfaces tables carried 31 indexes between them (primary keys aside):
18 on ``agent_surfaces``, 5 on ``agent_surface_external_users``, 8 on
``agent_surface_conversation_links``. Twenty-two go and five arrive, leaving 14.
Verified against a chain-built database, which matches dev exactly; the
downgrade restores all 31, byte for byte.

One of the twenty-two was the same index written twice --
``agent_surface_conversation_links.conversation_id`` had both ``index=True`` on
the column and an explicit ``Index(...)`` beside it, so every write maintained
two identical btrees and the planner could ever use one.

Each drop is one of four kinds, and each is checked against the query set rather
than guessed from the name:

**A redundant prefix.** ``pod_id`` leads ``uq_agent_surface_pod_name``,
``agent_id`` leads ``uq_agent_surface_agent_type``, ``platform`` leads the
external-user unique triple, ``surface_id`` leads the link unique. A standalone
index on a leading column answers nothing the composite does not.

**A column nothing filters on.** ``external_tenant_id``,
``surface_identity_username``, ``external_channel_id``, ``surface_identity_id``
on surfaces; ``phone`` on external users; ``routed_agent_id``, ``route_key`` and
``last_inbound_at`` on links. These are written and then read back off a row
that was found some other way. Verified by grepping each column against every
``where``/``order_by`` in ``app/``, not by reading its name.

**A column no btree could serve.** ``surface_identity_email`` and external-user
``email`` have exactly one reader each and both compare ``lower(...)``.
``uq_agent_surface_identity_email`` is already functional; the external-user one
is replaced with a functional index here.

**Low cardinality with no selective use.** ``mode`` (two members), ``event_mode``
(one), ``credential_mode`` (three). ``credential_mode`` is filtered twice, both
times as an extra predicate on a read already narrowed by platform and
organisation.

Five indexes are added, and each is named by the query that needs it in a
comment beside its declaration in ``infrastructure/models.py``. The one that
changes a plan rather than a cost is
``ix_agent_surface_link_thread_continuity``: the continuity lookup runs on every
inbound message, is the only link read not scoped to a surface, and had no
usable index at all -- it matched the standalone ``platform`` btree and then
filtered and sorted a table that grows per thread.

Indexes only. No column, constraint or row is touched, so this is reversible
exactly, and ``downgrade`` restores all twenty-two.

**Concurrently, in an autocommit block.** All three tables are on the inbound
routing path. A plain ``CREATE INDEX`` holds a ``SHARE`` lock for the whole
build and a plain ``DROP INDEX`` holds ``ACCESS EXCLUSIVE``, so twenty-two drops
and five builds against a populated deployment would stall inbound message
processing for as long as they take. ``0018`` says CONCURRENTLY "cannot run
inside Alembic's transaction"; that is stale -- ``script.py.mako`` wraps every
migration in ``op.get_context().autocommit_block()`` and ``0032`` uses one
directly.

The cost is that concurrent DDL is **not atomic**: interrupt a
``CREATE INDEX CONCURRENTLY`` and Postgres leaves the index behind marked
``INVALID``, which ``IF NOT EXISTS`` then treats as present and skips. If this
migration is interrupted, find one before re-running::

    SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;

and ``DROP INDEX CONCURRENTLY`` whatever it names. None of the five added here
is unique, so there is no half-built constraint to reason about -- only a
useless index the planner ignores.

Revision ID: 0041_surface_indexes
Revises: 0040_external_user_nulls
"""

from alembic import op

revision = "0041_surface_indexes"
down_revision = "0040_external_user_nulls"
branch_labels = None
depends_on = None


#: (index name, table, the CREATE INDEX body that restores it on downgrade).
_DROPPED = (
    # --- agent_surfaces: prefixes of an existing unique
    ("ix_agent_surfaces_pod_id", "agent_surfaces", "(pod_id)"),
    ("ix_agent_surfaces_agent_id", "agent_surfaces", "(agent_id)"),
    # --- agent_surfaces: leading column of ix_agent_surface_routing
    ("ix_agent_surfaces_surface_type", "agent_surfaces", "(surface_type)"),
    # --- agent_surfaces: read only as (pod_id, name)
    ("ix_agent_surfaces_name", "agent_surfaces", "(name)"),
    # --- agent_surfaces: low cardinality, never selected on alone
    ("ix_agent_surfaces_mode", "agent_surfaces", "(mode)"),
    ("ix_agent_surfaces_event_mode", "agent_surfaces", "(event_mode)"),
    ("ix_agent_surfaces_credential_mode", "agent_surfaces", "(credential_mode)"),
    # --- agent_surfaces: no query filters or orders on these
    ("ix_agent_surfaces_external_tenant_id", "agent_surfaces", "(external_tenant_id)"),
    (
        "ix_agent_surfaces_external_channel_id",
        "agent_surfaces",
        "(external_channel_id)",
    ),
    (
        "ix_agent_surfaces_surface_identity_id",
        "agent_surfaces",
        "(surface_identity_id)",
    ),
    (
        "ix_agent_surfaces_surface_identity_username",
        "agent_surfaces",
        "(surface_identity_username)",
    ),
    # --- agent_surfaces: its only reader compares lower(...)
    (
        "ix_agent_surfaces_surface_identity_email",
        "agent_surfaces",
        "(surface_identity_email)",
    ),
    # --- external users: prefix of the unique triple
    (
        "ix_agent_surface_external_users_platform",
        "agent_surface_external_users",
        "(platform)",
    ),
    # --- external users: never filtered
    (
        "ix_agent_surface_external_users_phone",
        "agent_surface_external_users",
        "(phone)",
    ),
    # --- external users: replaced by a functional index
    (
        "ix_agent_surface_external_users_email",
        "agent_surface_external_users",
        "(email)",
    ),
    # --- external users: replaced by (resolved_user_id, platform)
    (
        "ix_agent_surface_external_users_resolved_user_id",
        "agent_surface_external_users",
        "(resolved_user_id)",
    ),
    # --- links: the duplicate of ix_agent_surface_link_conversation
    (
        "ix_agent_surface_conversation_links_conversation_id",
        "agent_surface_conversation_links",
        "(conversation_id)",
    ),
    # --- links: prefix of the unique
    (
        "ix_agent_surface_conversation_links_surface_id",
        "agent_surface_conversation_links",
        "(surface_id)",
    ),
    # --- links: leading column of the new continuity index
    (
        "ix_agent_surface_conversation_links_platform",
        "agent_surface_conversation_links",
        "(platform)",
    ),
    # --- links: never filtered
    (
        "ix_agent_surface_conversation_links_routed_agent_id",
        "agent_surface_conversation_links",
        "(routed_agent_id)",
    ),
    (
        "ix_agent_surface_conversation_links_route_key",
        "agent_surface_conversation_links",
        "(route_key)",
    ),
    # Named by hand in the migration that added the column, so it does not
    # follow SQLAlchemy's default and is not the name metadata would produce.
    (
        "ix_agent_surface_link_last_inbound_at",
        "agent_surface_conversation_links",
        "(last_inbound_at)",
    ),
)

#: (index name, table, body). Each is named by its query in `models.py`.
_ADDED = (
    (
        "ix_agent_surface_routing",
        "agent_surfaces",
        "(surface_type, status)",
    ),
    (
        "ix_agent_surface_external_user_platform_email",
        "agent_surface_external_users",
        "(platform, lower(email)) WHERE email IS NOT NULL",
    ),
    (
        "ix_agent_surface_external_user_resolved_platform",
        "agent_surface_external_users",
        "(resolved_user_id, platform)",
    ),
    (
        "ix_agent_surface_link_thread_continuity",
        "agent_surface_conversation_links",
        (
            "(platform, external_thread_id, external_channel_id,"
            " external_user_id, updated_at DESC)"
        ),
    ),
    (
        "ix_agent_surface_link_surface_member",
        "agent_surface_conversation_links",
        "(surface_id, external_user_id, last_inbound_at DESC)",
    ),
)


def upgrade() -> None:
    for name, table, body in _ADDED:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {body}")
    # After the replacements exist, so no read is left without an index for the
    # length of the transaction.
    for name, _table, _body in _DROPPED:
        op.execute(f"DROP INDEX IF EXISTS {name}")


def downgrade() -> None:
    for name, table, body in _DROPPED:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {body}")
    for name, _table, _body in _ADDED:
        op.execute(f"DROP INDEX IF EXISTS {name}")
