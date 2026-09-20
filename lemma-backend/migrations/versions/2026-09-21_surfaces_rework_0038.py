"""The surfaces rework: private onboarding, one door per agent, and real indexes.

Five revisions (`0038`–`0042`) collapsed into one. None of them shipped — the
last release tag ends at `0030_usage_requests` — so what reaches a deployment is
the destination rather than the path this took to reach it. Kept separate from
`0037_email_challenges`, which is identity's, because one revision per module
boundary is the readable unit.

Four things happen here, and they are four different reasons.

**Private onboarding gets tables.** `surface_verified_identities`,
`surface_pending_onboarding` and `surface_onboarding_input_tokens`. The first
carries a check constraint rather than a convention: a revoked identity cannot
keep a destination. That was two tables keyed on the same binding which could
disagree, and a live route beside a revoked identity was a state every reader
had to exclude by hand. Here it is a row the database will not accept. The
route's foreign keys are `SET NULL`, not `CASCADE`: removing a company's Slack
app should cost its people a destination, not the proof of who they are —
PS-SURF-005 promises they resume without another email code while their
verified identity holds, and a cascade deletes the row holding it.

`surface_pending_onboarding.step` is pinned by a check constraint because
`OnboardingStep`'s own docstring says a typo there "is not an error, it is a
state the dispatcher silently has no branch for". Safe to pin, unlike
`agent_surfaces.surface_type`, because these rows are short-lived and internal —
no retired value has to survive in one.

**One surface per agent per platform** (`uq_agent_surface_agent_type`). An agent
reaches a platform in exactly one place: one Slack app, one WhatsApp number, one
Telegram bot. Nothing said so before, and the rule that *was* enforced is a
different one — a connected account is claimable once per organisation — which
permits an agent holding several. Two doors onto one platform for one agent is
an ambiguity, not a feature: whoever is on the other side cannot tell which one
they are talking to.

**External-user identity applies to Telegram too.** The unique index over
(platform, tenant_id, external_user_id) never applied to Telegram, which writes
`tenant_id` NULL, because Postgres treats NULLs as distinct. The full argument
for `NULLS NOT DISTINCT` now lives on
`infrastructure/repositories/external_user_repository.py`, whose correctness
depends on it — the `scalar_one_or_none` read and the savepoint retry both
assume the constraint holds. A migration is the wrong place for a rule the code
has to keep obeying long after the migration has run.

**The indexes match the queries.** The three surfaces tables carried 31 indexes
between them; 21 go and 5 arrive, leaving 15. One of the 21 was the same index
written twice — `agent_surface_conversation_links.conversation_id` had both
`index=True` on the column and an explicit `Index(...)` beside it, so every
write maintained two identical btrees and the planner could only ever use one.
Each drop is a redundant prefix of an existing composite, a column nothing
filters on, a column no btree could serve because its only reader compares
`lower(...)`, or low cardinality with no selective use. Each was checked against
the query set by grepping the column against every `where`/`order_by` in `app/`,
not guessed from its name. Every index added is named by the query that needs it
in a comment beside its declaration in `infrastructure/models.py`; an index with
no named query does not get added.

The one that changes a plan rather than a cost is
`ix_agent_surface_link_thread_continuity`: the continuity lookup runs on every
inbound message, is the only link read not scoped to a surface, and had no usable
index at all — it matched the standalone `platform` btree and then filtered and
sorted a table that grows per thread.

**`agent_surfaces.mode` is dropped.** A two-member enum derived from
`surface_type`, validated against it, then read back to re-derive it. No API ever
accepted it. It had already misled the codebase once: `ThreadShape`'s docstring
records that asking `surface.mode` gave a channel thread the DM reset.

## Two phases, and why

Everything that carries **correctness** is transactional: the tables, the check
constraints, the three unique indexes, the dropped column. Losing uniqueness for
the length of a concurrent build is worse than holding a lock for it, so no
unique index here is built `CONCURRENTLY`.

Everything that is only a **cost** — the 21 drops and the 5 non-unique adds —
runs `CONCURRENTLY` in an autocommit block. All three tables are on the inbound
routing path: a plain `CREATE INDEX` holds `SHARE` for the whole build and a
plain `DROP INDEX` holds `ACCESS EXCLUSIVE`, which against a populated
deployment stalls inbound message processing for as long as they take. (`0018`
says CONCURRENTLY "cannot run inside Alembic's transaction"; that is stale —
`script.py.mako` wraps the migrations it *generates* in
`op.get_context().autocommit_block()`, and this hand-written one opens its own.)

The cost of that second phase is that it is **not atomic**, and entering an
autocommit block commits the first phase. Interrupt a
`CREATE INDEX CONCURRENTLY` and Postgres leaves the index behind marked
`INVALID`, which `IF NOT EXISTS` then treats as present and skips. So every
statement in phase two is idempotent and the revision is safe to re-run — but
find the invalid ones first::

    SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;

and `DROP INDEX CONCURRENTLY` whatever it names. None of the five added
concurrently is unique, so there is no half-built constraint to reason about —
only a useless index the planner ignores.

Revision ID: 0038_surfaces_rework
Revises: 0037_email_challenges
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0038_surfaces_rework"
down_revision = "0037_email_challenges"
branch_labels = None
depends_on = None


_AGENT_TYPE_CONSTRAINT = "uq_agent_surface_agent_type"
_EXTERNAL_USER_INDEX = "ix_agent_surface_external_user_platform_tenant_external"
_EXTERNAL_USER_COLUMNS = "platform, tenant_id, external_user_id"

#: (index name, table, the CREATE INDEX body that restores it on downgrade).
#: `ix_agent_surfaces_mode` is absent on purpose: dropping the column takes its
#: index with it, and listing it here would have the downgrade rebuild an index
#: on a column that does not exist yet.
_DROPPED = (
    # --- agent_surfaces: prefixes of an existing unique
    ("ix_agent_surfaces_pod_id", "agent_surfaces", "(pod_id)"),
    ("ix_agent_surfaces_agent_id", "agent_surfaces", "(agent_id)"),
    # --- agent_surfaces: leading column of ix_agent_surface_routing
    ("ix_agent_surfaces_surface_type", "agent_surfaces", "(surface_type)"),
    # --- agent_surfaces: read only as (pod_id, name)
    ("ix_agent_surfaces_name", "agent_surfaces", "(name)"),
    # --- agent_surfaces: low cardinality, never selected on alone
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

#: (index name, table, body). Each is named by its query in `models.py`. None is
#: unique, which is what makes building them concurrently free of consequence.
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


def _refuse_duplicate_agent_surfaces() -> None:
    """Report the agents holding two doors, rather than fail on one of them.

    Checked before the index is built so a failure names the agents involved
    rather than arriving as a bare unique violation about one arbitrary row.
    Which of two surfaces to keep is a decision for whoever owns the data.
    """
    offenders = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT agent_id, surface_type, count(*) AS rows "
                "FROM agent_surfaces GROUP BY agent_id, surface_type "
                "HAVING count(*) > 1 ORDER BY rows DESC"
            )
        )
        .fetchall()
    )
    if not offenders:
        return
    listed = ", ".join(
        f"agent {row.agent_id} holds {row.rows} {row.surface_type} surfaces"
        for row in offenders[:10]
    )
    raise RuntimeError(
        "Cannot enforce one surface per agent per platform while duplicates "
        f"exist: {listed}"
        + (f" (and {len(offenders) - 10} more)" if len(offenders) > 10 else "")
        + ". Delete the surfaces that should not have been created, then run "
        "this migration again."
    )


def _refuse_duplicate_external_users() -> None:
    """Report senders cached twice, rather than fail on one of them.

    Duplicates can already exist on any deployment that has run Telegram. Which
    of two cache rows to keep is a judgement about someone's identity, and not
    one a migration should make: the rows may name different users.
    """
    offenders = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT platform, external_user_id, count(*) AS rows "
                "FROM agent_surface_external_users "
                f"GROUP BY {_EXTERNAL_USER_COLUMNS} "
                "HAVING count(*) > 1 ORDER BY rows DESC"
            )
        )
        .fetchall()
    )
    if not offenders:
        return
    listed = ", ".join(
        f"{row.platform} sender {row.external_user_id} has {row.rows} rows"
        for row in offenders[:10]
    )
    raise RuntimeError(
        "Cannot make external-user identity unique while duplicates exist: "
        f"{listed}"
        + (f" (and {len(offenders) - 10} more)" if len(offenders) > 10 else "")
        + ". Keep the row whose resolved_user_id is correct, delete the rest, "
        "then run this migration again."
    )


def _create_onboarding_tables() -> None:
    op.create_table(
        "surface_verified_identities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("binding_key", sa.String(64), nullable=False, unique=True),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("external_user_id", sa.String(255), nullable=False),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("verified_phone", sa.String(32), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        # Where this identity talks. Null until a workspace is chosen: being
        # recognised and having somewhere to talk are different things.
        sa.Column(
            "installation_surface_id",
            sa.Uuid(),
            sa.ForeignKey("agent_surfaces.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "pod_id",
            sa.Uuid(),
            sa.ForeignKey("pods.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR ("
            "installation_surface_id IS NULL AND pod_id IS NULL)",
            name="ck_surface_identity_route_is_live",
        ),
    )
    op.create_index(
        "ix_surface_verified_identities_user_id",
        "surface_verified_identities",
        ["user_id"],
    )
    op.create_table(
        "surface_pending_onboarding",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("binding_key", sa.String(64), nullable=False, unique=True),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("step", sa.String(32), nullable=False),
        sa.CheckConstraint(
            "step IN ('handoff', 'awaiting_phone', 'awaiting_email', "
            "'awaiting_code', 'verified', 'awaiting_pod', "
            "'organization_access_required', 'ready', 'cancelled', 'expired')",
            name="ck_pending_onboarding_step",
        ),
        sa.Column("challenge_id", sa.Uuid(), nullable=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "installation_surface_id",
            sa.Uuid(),
            sa.ForeignKey("agent_surfaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("verified_phone", sa.String(32), nullable=True),
        sa.Column("destination", postgresql.JSONB(), nullable=False),
        sa.Column("original_event", postgresql.JSONB(), nullable=True),
        sa.Column("offered_pods", postgresql.JSONB(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handed_off_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message_committed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_surface_pending_onboarding_expires_at",
        "surface_pending_onboarding",
        ["expires_at"],
    )
    op.create_table(
        "surface_onboarding_input_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("challenge_id", sa.Uuid(), nullable=True),
        sa.Column(
            "pending_id",
            sa.Uuid(),
            sa.ForeignKey("surface_pending_onboarding.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Both sweeps run every sixty seconds, forever. Without these they are two
    # sequential scans a minute for the life of the deployment.
    op.create_index(
        "ix_surface_onboarding_input_tokens_expires_at",
        "surface_onboarding_input_tokens",
        ["expires_at"],
    )


def upgrade() -> None:
    # --- Phase one: everything that carries correctness, in one transaction.
    _create_onboarding_tables()

    _refuse_duplicate_agent_surfaces()
    op.create_unique_constraint(
        _AGENT_TYPE_CONSTRAINT, "agent_surfaces", ["agent_id", "surface_type"]
    )

    _refuse_duplicate_external_users()
    op.drop_index(_EXTERNAL_USER_INDEX, table_name="agent_surface_external_users")
    op.execute(
        f"CREATE UNIQUE INDEX {_EXTERNAL_USER_INDEX} ON "
        f"agent_surface_external_users ({_EXTERNAL_USER_COLUMNS}) "
        "NULLS NOT DISTINCT"
    )

    # One agent per pooled WhatsApp number. The constraint above stops an agent
    # holding two numbers; without this nothing stops two agents holding one --
    # and with a pool the arriving number *is* the routing key, so two claimants
    # on one number is an inbound message with no answer to "which agent".
    # Partial and scoped to WhatsApp: a Slack or Teams bot id may legitimately
    # repeat across system-credential surfaces.
    op.execute(
        "CREATE UNIQUE INDEX uq_agent_pooled_whatsapp_number "
        "ON agent_surfaces (surface_identity_id) "
        "WHERE surface_type = 'WHATSAPP' AND surface_identity_id IS NOT NULL"
    )

    # Takes `ix_agent_surfaces_mode` with it, which is why that index is not in
    # `_DROPPED`.
    op.drop_column("agent_surfaces", "mode")

    # --- Phase two: cost only. Entering this block commits the above.
    with op.get_context().autocommit_block():
        for name, table, body in _ADDED:
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} {body}"
            )
        # After the replacements exist, so no read is left without an index.
        for name, _table, _body in _DROPPED:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table, body in _DROPPED:
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} {body}"
            )
        for name, _table, _body in _ADDED:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")

    op.add_column(
        "agent_surfaces",
        sa.Column(
            "mode",
            sa.String(length=50),
            nullable=False,
            server_default="DM",
        ),
    )
    # The value it always held: derived from the platform, never from a caller.
    op.execute("UPDATE agent_surfaces SET mode = 'EMAIL' WHERE surface_type = 'RESEND'")
    op.execute("CREATE INDEX ix_agent_surfaces_mode ON agent_surfaces (mode)")

    op.execute("DROP INDEX IF EXISTS uq_agent_pooled_whatsapp_number")
    op.drop_index(_EXTERNAL_USER_INDEX, table_name="agent_surface_external_users")
    op.create_index(
        _EXTERNAL_USER_INDEX,
        "agent_surface_external_users",
        ["platform", "tenant_id", "external_user_id"],
        unique=True,
    )
    op.drop_constraint(_AGENT_TYPE_CONSTRAINT, "agent_surfaces", type_="unique")

    op.drop_table("surface_onboarding_input_tokens")
    op.drop_table("surface_pending_onboarding")
    op.drop_table("surface_verified_identities")
