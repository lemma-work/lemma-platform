"""A pool of WhatsApp numbers, each independent, several orgs may hold one.

Until now a deployment had exactly one WhatsApp number, named in settings. Every
surface sent from it, every webhook arrived on it, and the one organisation rule
was "a system WhatsApp credential is claimable once per organisation" -- which is
a rule about a credential, not about a number, because there was only ever one.

Three things arrive here.

**`surface_whatsapp_numbers`, and every number is independent.** Its own
`waba_id`, `access_token`, `verify_token` and Flow ids, each nullable and falling
back to settings so a one-number deployment declares nothing and keeps working.
Secrets are encrypted through `app.core.crypto` exactly as
`agent_surfaces.webhook_secret` is.

`app_secret` is here too, and it is the one field that is *not* per number: Meta
computes `X-Hub-Signature-256` with the **app** secret, so numbers co-tenanted
under one Meta app necessarily share it. Storing it per row means duplicate
values, which is honest -- the column says "the secret to verify this number's
deliveries with", and two numbers under one app simply answer the same.

There is no column saying what a number is *for*. Every row is a number this
deployment owns and sends from, and an earlier draft split them `SHARED` (the
line in settings) and `ALLOCATABLE` (the pool) -- a distinction that changed
nothing about how a number behaves, only whether allocation would offer it.
`status` already says that, and says it better: `AVAILABLE` or `RETIRED`,
because "stop handing this out" and "we no longer own it" are different facts
and conflating them loses the ability to say the first.

The cold-open line -- what identity's phone verification sends from, and what a
surface holding no number of its own answers with -- is therefore not a row that
claims to be it. It is the number in settings, and where settings are silent,
the oldest available row. A deployment configured the old way keeps the line it
has; one configured entirely through the pool has a line too, and neither has to
declare which row is special.

There is deliberately **no `allocated_surface_id`**. The holder already lives on
`agent_surfaces.surface_identity_id`; a second copy is two places that can
disagree about who holds a scarce thing, and the one that disagrees silently is
the one that keeps a number allocated to a surface that no longer exists.

**`agent_surfaces.organization_id`, and it cannot go stale.** Per-organisation
uniqueness is over (organisation, number), and the organisation was reachable
only by joining `pods`. Denormalising it invites the usual bug -- a pod moves
organisation and the copy does not -- so it is not merely backfilled and trusted:
a composite foreign key `(pod_id, organization_id) -> pods (id, organization_id)`
`ON UPDATE CASCADE` makes a wrong pair unrepresentable and a moved pod update its
surfaces itself. That FK needs a unique on the parent side, which `pods` did not
have (`id` alone is the primary key), so one is added over `(id, organization_id)`.

**The uniqueness rule changes shape.** `uq_agent_pooled_whatsapp_number` was
unique on `surface_identity_id` deployment-wide, which reads "one surface per
number, everywhere" -- the opposite of a pool shared across organisations. It
becomes `uq_agent_org_whatsapp_number` over `(organization_id,
surface_identity_id)`: several organisations may hold one number, and within an
organisation exactly one surface does.

Dropping the old index costs nothing to verify, because it has never constrained
a row: `SurfaceAccountBindingResolver.resolve_binding` returns no identity for
WhatsApp on every path, so `surface_identity_id` is NULL for every WhatsApp
surface that exists. The index is a shipped, empty reservation of exactly this
slot, and this is the revision that fills it.

**Why the number alone is not the routing key.** Two organisations may hold one
number, so an arriving number is ambiguous by construction. Routing resolves the
*sender* first and uses the number as an additional predicate on candidates
already narrowed to the sender's pods; the pair is at most one surface per
organisation the sender belongs to. Stated here because the schema is what makes
the ambiguity possible, and a later reader finding a non-unique number will want
to know it was intended.

Revision ID: 0039_whatsapp_number_pool
Revises: 0038_surfaces_rework
"""

import sqlalchemy as sa
from alembic import op

revision = "0039_whatsapp_number_pool"
down_revision = "0038_surfaces_rework"
branch_labels = None
depends_on = None


_POOL_TABLE = "surface_whatsapp_numbers"
_OLD_NUMBER_INDEX = "uq_agent_pooled_whatsapp_number"
_NEW_NUMBER_INDEX = "uq_agent_org_whatsapp_number"
_POD_IDENTITY = "uq_pod_id_organization"
_SURFACE_ORG_FK = "fk_agent_surface_pod_organization"


_PENDING_STEPS = (
    "'handoff', 'awaiting_phone', 'awaiting_email', 'awaiting_code', "
    "'verified', 'awaiting_pod', 'organization_access_required', 'ready', "
    "'cancelled', 'expired'"
)
#: `refused` joins them: a signup that cannot be finished now ends instead of
#: refusing forever. `0038` pinned the step values by check constraint on the
#: deliberate ground that a typo there "is not an error, it is a state the
#: dispatcher silently has no branch for" -- which is exactly why adding a
#: member to `OnboardingStep` without touching the constraint turns the new
#: terminal state into a `CheckViolation` at the moment it is written.
_REFUSED_STEP = "'refused'"


def _pin_pending_steps(values: str) -> None:
    op.execute(
        "ALTER TABLE surface_pending_onboarding "
        "DROP CONSTRAINT IF EXISTS ck_pending_onboarding_step"
    )
    op.execute(
        "ALTER TABLE surface_pending_onboarding ADD CONSTRAINT "
        f"ck_pending_onboarding_step CHECK (step IN ({values}))"
    )


def upgrade() -> None:
    _pin_pending_steps(f"{_PENDING_STEPS}, {_REFUSED_STEP}")
    op.create_table(
        _POOL_TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        # The routing key: what Meta puts in `metadata.phone_number_id` on every
        # delivery, and what addresses the Graph API. Opaque, never the E.164
        # number -- Meta normalises a literal `+` in a URL path to a space.
        sa.Column("phone_number_id", sa.String(64), nullable=False, unique=True),
        # What a person sees as the sender. Resolved from Graph by the admin
        # tool at add time, so allocation itself never makes an API call.
        sa.Column("display_phone_number", sa.String(32), nullable=False, unique=True),
        sa.Column("waba_id", sa.String(64), nullable=False),
        # Each nullable: absent means "fall back to settings", which is what
        # makes a one-number deployment need no rows at all.
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("app_secret", sa.Text(), nullable=True),
        sa.Column("verify_token", sa.Text(), nullable=True),
        # Flow assets are WABA-scoped, so they belong to the number rather than
        # to the deployment.
        sa.Column("onboarding_email_flow_id", sa.String(64), nullable=True),
        sa.Column("onboarding_code_flow_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('AVAILABLE', 'RETIRED')", name="ck_whatsapp_number_status"
        ),
    )
    # Allocation reads "an AVAILABLE number this organisation does not hold", and
    # the cold-open fallback reads "the oldest AVAILABLE number". Both start
    # here; the "this org does not hold it" half is answered by `agent_surfaces`.
    op.create_index(
        "ix_whatsapp_number_allocatable",
        _POOL_TABLE,
        ["status", "created_at"],
    )

    # --- The organisation a surface belongs to, carried rather than joined.
    op.execute(
        f"ALTER TABLE pods ADD CONSTRAINT {_POD_IDENTITY} UNIQUE (id, organization_id)"
    )
    op.add_column(
        "agent_surfaces",
        sa.Column("organization_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        "UPDATE agent_surfaces SET organization_id = pods.organization_id "
        "FROM pods WHERE pods.id = agent_surfaces.pod_id"
    )
    # Every surface has a pod and every pod has an organisation, so the backfill
    # is total. If it is not, the NOT NULL below is the thing that says so
    # rather than a NULL quietly surviving into the uniqueness rule, where it
    # would make the row invisible to it.
    op.alter_column("agent_surfaces", "organization_id", nullable=False)
    op.execute(
        f"ALTER TABLE agent_surfaces ADD CONSTRAINT {_SURFACE_ORG_FK} "
        "FOREIGN KEY (pod_id, organization_id) "
        "REFERENCES pods (id, organization_id) ON UPDATE CASCADE"
    )

    # --- One number per organisation, instead of one per deployment.
    op.execute(f"DROP INDEX IF EXISTS {_OLD_NUMBER_INDEX}")
    op.execute(
        f"CREATE UNIQUE INDEX {_NEW_NUMBER_INDEX} "
        "ON agent_surfaces (organization_id, surface_identity_id) "
        "WHERE surface_type = 'WHATSAPP' AND surface_identity_id IS NOT NULL"
    )


def downgrade() -> None:
    _pin_pending_steps(_PENDING_STEPS)
    op.execute(f"DROP INDEX IF EXISTS {_NEW_NUMBER_INDEX}")
    # Restoring the deployment-global rule can only fail where two organisations
    # hold one number -- precisely what this revision made legal. Reported by
    # hand, as `0038`'s two pre-flight checks report theirs, so a rollback names
    # the numbers rather than arriving as a bare index-build failure about one
    # arbitrary row. Which organisation keeps a number is a commercial decision,
    # and not one a downgrade should make by picking a row.
    shared = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT surface_identity_id, count(DISTINCT organization_id) AS orgs "
                "FROM agent_surfaces "
                "WHERE surface_type = 'WHATSAPP' AND surface_identity_id IS NOT NULL "
                "GROUP BY surface_identity_id HAVING count(DISTINCT organization_id) > 1 "
                "ORDER BY orgs DESC"
            )
        )
        .fetchall()
    )
    if shared:
        listed = ", ".join(
            f"number {row.surface_identity_id} is held by {row.orgs} organisations"
            for row in shared[:10]
        )
        raise RuntimeError(
            "Cannot restore one-organisation-per-number while the pool is in "
            f"use: {listed}"
            + (f" (and {len(shared) - 10} more)" if len(shared) > 10 else "")
            + ". Decide which organisation keeps each number, clear "
            "`surface_identity_id` on the others, then run this downgrade again."
        )
    op.execute(
        f"CREATE UNIQUE INDEX {_OLD_NUMBER_INDEX} "
        "ON agent_surfaces (surface_identity_id) "
        "WHERE surface_type = 'WHATSAPP' AND surface_identity_id IS NOT NULL"
    )

    op.execute(
        f"ALTER TABLE agent_surfaces DROP CONSTRAINT IF EXISTS {_SURFACE_ORG_FK}"
    )
    op.drop_column("agent_surfaces", "organization_id")
    op.execute(f"ALTER TABLE pods DROP CONSTRAINT IF EXISTS {_POD_IDENTITY}")

    op.drop_index("ix_whatsapp_number_allocatable", table_name=_POOL_TABLE)
    op.drop_table(_POOL_TABLE)
