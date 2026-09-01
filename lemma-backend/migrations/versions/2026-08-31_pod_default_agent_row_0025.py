"""Give the pod's own assistant a row, and let a schedule say what to do.

The assistant was the one agent with no ``agents`` row. It was synthesised at
run time from a conversation whose ``agent_id`` is null, against a single
sentinel id shared by every pod. Nothing could point a foreign key at it, so
everything that needed to name it grew its own way of saying so: a third target
column on ``schedules``, a boolean on a channel route, a magic string in a map
of who answers whose DMs, and a ``COALESCE`` with the sentinel written into the
definition of an index.

So it gets a row. ``agents.id = pods.id`` for that row, stated as an
equivalence against ``kind`` rather than left as a convention -- which is also
what makes the primary key enforce one-per-pod, with no extra unique index. The
id being derivable from the pod matters beyond tidiness: the per-request check
"is this actor the pod's own assistant?" stays a comparison instead of becoming
a query.

Also here, and unrelated except that it shipped in the same change:
``schedules.instruction`` -- what the target should *do* when a schedule fires,
as against ``filter_instruction``, which decides whether to fire at all.

Operator note: step 6 rewrites every conversation row that has no agent. That
UPDATE is not HOT -- each row rewrites a heap tuple, inserts two index entries
and fires a referential-integrity check -- and Alembic runs a migration in one
transaction, so it cannot commit in batches here. On an installation where that
table is large, run the batched form first, out of band:

    UPDATE agent_conversations SET agent_id = pod_id
     WHERE id IN (SELECT id FROM agent_conversations
                   WHERE agent_id IS NULL LIMIT 5000);

in a loop until it reports zero. Because the statement below is guarded by
``WHERE agent_id IS NULL``, the migration then finds nothing left to do.

Rollback is exact but not lossless. ``agent_id = pod_id`` is unambiguous -- no
other agent can hold that id, by the constraint -- so the downgrade restores
every null it set. What it cannot restore is a schedule authored against the
assistant *after* this ran: ``targets_pod_default`` did not exist at 0024, so
such a schedule downgrades to one with no target at all, and quietly fires
nothing. Said here because nothing else will say it.
"""

import sqlalchemy as sa
from alembic import op


revision = "0025_pod_default_agent_row"
down_revision = "0024_drop_surface_schedule_id"
branch_labels = None
depends_on = None


_SENTINEL = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    # --- 1. guard -----------------------------------------------------------
    # An agents row already holding its pod's id would violate the identity
    # check the moment it is added, and would surface as an opaque constraint
    # violation halfway through a migration that has already written. Ids are
    # uuid7 so this cannot happen by chance, which is the point: if it ever
    # does, something minted a row deliberately and a human should look.
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM agents WHERE id = pod_id) THEN "
            "RAISE EXCEPTION 'an agents row already has id = pod_id; "
            "resolve it before migrating (see 0025)'; "
            "END IF; END $$;"
        )
    )

    # --- 2. the kind ---------------------------------------------------------
    op.add_column(
        "agents",
        sa.Column(
            "kind",
            sa.String(length=30),
            nullable=False,
            server_default=sa.text("'USER'"),
        ),
    )
    op.create_check_constraint(
        "ck_agents_kind", "agents", "kind IN ('USER', 'POD_DEFAULT')"
    )

    # --- 3. the name stops being forbidden and starts being required ---------
    # The old constraint reserved `pod_default` against everybody while nothing
    # could hold it. Memory folder slugs derive from `agent.name`, and every
    # pod's assistant notes already live under that slug, so the row has to
    # carry exactly that name or the notes are orphaned.
    op.drop_constraint(
        "ck_agents_name_not_pod_default_selector", "agents", type_="check"
    )

    # --- 4. one row per pod, including soft-deleted ones ---------------------
    # `delete_pod` renames and flags rather than deleting, and a pod that is
    # still readable must not be the one pod in the fleet with no assistant.
    op.execute(
        sa.text(
            "INSERT INTO agents (id, pod_id, user_id, name, kind, instruction, "
            "toolsets, visibility, created_at, updated_at) "
            "SELECT p.id, p.id, p.user_id, 'pod_default', 'POD_DEFAULT', '', "
            "'[]'::jsonb, 'POD', p.created_at, now() "
            "FROM pods p "
            "ON CONFLICT (id) DO NOTHING"
        )
    )

    # --- 5. the constraints that make the convention an invariant -----------
    op.create_check_constraint(
        "ck_agents_pod_default_is_pod_id",
        "agents",
        "(kind = 'POD_DEFAULT') = (id = pod_id)",
    )
    op.create_check_constraint(
        "ck_agents_name_not_pod_default_selector",
        "agents",
        "(kind = 'POD_DEFAULT') = (name = 'pod_default')",
    )
    op.create_check_constraint(
        "ck_agents_pod_default_immutable",
        "agents",
        "kind <> 'POD_DEFAULT' OR ("
        "instruction = '' AND toolsets = '[]'::jsonb "
        "AND visibility = 'POD' AND agent_runtime IS NULL"
        ")",
    )

    # --- 6. the index, before the backfill ----------------------------------
    # `COALESCE(agent_id, pod_id)` is invariant under the backfill below: every
    # row's indexed value is identical before and after, which is exactly why
    # the same expression serves rows that were backfilled and rows an older
    # process is still writing as null. It also means an interrupted backfill
    # leaves a logically correct index.
    # Built in the migration's own transaction rather than CONCURRENTLY. The
    # concurrent form cannot run inside one, and taking it would mean this
    # migration commits in pieces -- so a failure in the backfill below would
    # leave the rows inserted, the revision unstamped, and a re-run tripping
    # over its own guard. A half-applied schema change is worse than a slow
    # one, and the operator note above is how a large installation avoids the
    # slowness instead.
    op.create_index(
        "ix_agent_conv_user_pod_agent_roots_v2",
        "agent_conversations",
        ["user_id", "pod_id", sa.text("COALESCE(agent_id, pod_id)"), "id"],
        unique=False,
        postgresql_where=sa.text("parent_id IS NULL"),
    )
    op.drop_index(
        "ix_agent_conv_user_pod_agent_roots", table_name="agent_conversations"
    )

    # --- 7. the backfill ----------------------------------------------------
    op.execute(
        sa.text(
            "UPDATE agent_conversations SET agent_id = pod_id WHERE agent_id IS NULL"
        )
    )
    # Runs follow their conversation on the write path; existing rows do not.
    op.execute(
        sa.text(
            "UPDATE agent_runs r SET agent_id = c.pod_id "
            "FROM agent_conversations c "
            "WHERE r.conversation_id = c.id AND r.agent_id IS NULL"
        )
    )

    # --- 8. surfaces belong to exactly one agent ------------------------------
    # A surface's `agent_id` used to answer two questions at once. Inbound it
    # meant "who answers here by default", and null meant the assistant.
    # Outbound it meant "whose bot is this", and null meant nobody's -- so any
    # agent could borrow it. One column, two meanings, and the same rows.
    #
    # Now it answers one question: whose surface this is. Null becomes the
    # assistant's row like everywhere else, and the column stops being nullable
    # so the ambiguity is unrepresentable rather than merely discouraged.
    op.execute(
        sa.text("UPDATE agent_surfaces SET agent_id = pod_id WHERE agent_id IS NULL")
    )
    op.alter_column("agent_surfaces", "agent_id", nullable=False)
    # Was ON DELETE SET NULL, and that is what produced a bug the code had to
    # carry a cleanup routine for: a deleted agent's mailbox became an agentless
    # surface -- which is exactly what the assistant's own surface was -- so the
    # pod started answering from a deleted agent's address. A surface cannot
    # outlive its agent.
    op.drop_constraint(
        "agent_surfaces_agent_id_fkey", "agent_surfaces", type_="foreignkey"
    )
    op.create_foreign_key(
        "agent_surfaces_agent_id_fkey",
        "agent_surfaces",
        "agents",
        ["agent_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # --- 9. the schedule half ------------------------------------------------
    op.add_column("schedules", sa.Column("instruction", sa.Text(), nullable=True))
    # Two arms again, not three: `agent_id` can now name the assistant like any
    # other agent, so there is nothing for a third column to say.
    op.create_check_constraint(
        "ck_schedules_single_target",
        "schedules",
        "(agent_id IS NOT NULL)::int + (workflow_id IS NOT NULL)::int <= 1",
    )


def downgrade() -> None:
    op.drop_constraint("ck_schedules_single_target", "schedules", type_="check")
    op.drop_column("schedules", "instruction")

    op.drop_constraint(
        "agent_surfaces_agent_id_fkey", "agent_surfaces", type_="foreignkey"
    )
    op.create_foreign_key(
        "agent_surfaces_agent_id_fkey",
        "agent_surfaces",
        "agents",
        ["agent_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.alter_column("agent_surfaces", "agent_id", nullable=True)
    op.execute(
        sa.text("UPDATE agent_surfaces SET agent_id = NULL WHERE agent_id = pod_id")
    )

    op.create_index(
        "ix_agent_conv_user_pod_agent_roots",
        "agent_conversations",
        [
            "user_id",
            "pod_id",
            sa.text(f"COALESCE(agent_id, '{_SENTINEL}'::uuid)"),
            "id",
        ],
        unique=False,
        postgresql_where=sa.text("parent_id IS NULL"),
    )
    op.drop_index(
        "ix_agent_conv_user_pod_agent_roots_v2", table_name="agent_conversations"
    )

    # Un-point everything before deleting the rows. `schedules.agent_id` is ON
    # DELETE CASCADE, so deleting first would take every assistant-targeted
    # schedule with it rather than leaving it targetless.
    op.execute(
        sa.text(
            "UPDATE schedules SET agent_id = NULL WHERE agent_id IN "
            "(SELECT id FROM agents WHERE kind = 'POD_DEFAULT')"
        )
    )
    op.execute(
        sa.text(
            "UPDATE agent_runs SET agent_id = NULL WHERE agent_id IN "
            "(SELECT id FROM agents WHERE kind = 'POD_DEFAULT')"
        )
    )
    op.execute(
        sa.text(
            "UPDATE agent_conversations SET agent_id = NULL WHERE agent_id = pod_id"
        )
    )
    op.execute(sa.text("DELETE FROM agents WHERE kind = 'POD_DEFAULT'"))

    op.drop_constraint("ck_agents_pod_default_immutable", "agents", type_="check")
    op.drop_constraint(
        "ck_agents_name_not_pod_default_selector", "agents", type_="check"
    )
    op.drop_constraint("ck_agents_pod_default_is_pod_id", "agents", type_="check")
    op.create_check_constraint(
        "ck_agents_name_not_pod_default_selector",
        "agents",
        "name NOT IN ('POD_DEFAULT', 'pod_default')",
    )
    op.drop_constraint("ck_agents_kind", "agents", type_="check")
    op.drop_column("agents", "kind")
