from __future__ import annotations

SQLITE_MIGRATIONS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (
        1,
        "record_legacy_core_schema",
        (),
    ),
    (
        2,
        "sandbox_desired_and_observed_state",
        (
            "ALTER TABLE sandboxes ADD COLUMN desired_state TEXT NOT NULL DEFAULT 'present'",
            "ALTER TABLE sandboxes ADD COLUMN desired_generation INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE sandboxes ADD COLUMN observed_generation INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE sandboxes ADD COLUMN provider_name TEXT",
            "ALTER TABLE sandboxes ADD COLUMN provider_id TEXT",
            "ALTER TABLE sandboxes ADD COLUMN instance_id TEXT",
            "ALTER TABLE sandboxes ADD COLUMN last_active_at REAL",
            "ALTER TABLE sandboxes ADD COLUMN last_observed_at REAL",
        ),
    ),
    (
        3,
        "activity_leases_lifecycle_claims_and_orphans",
        (
            """
            CREATE TABLE IF NOT EXISTS agentbox_activity_leases (
                lease_id TEXT PRIMARY KEY,
                sandbox_id TEXT NOT NULL,
                session_id TEXT,
                operation TEXT NOT NULL,
                owner TEXT NOT NULL,
                expires_at REAL NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY (sandbox_id) REFERENCES sandboxes(sandbox_id)
                    ON DELETE CASCADE
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS agentbox_activity_leases_expiry_idx
            ON agentbox_activity_leases (expires_at)
            """,
            """
            CREATE TABLE IF NOT EXISTS agentbox_lifecycle_claims (
                sandbox_id TEXT PRIMARY KEY,
                claim_id TEXT NOT NULL UNIQUE,
                operation TEXT NOT NULL,
                owner TEXT NOT NULL,
                expires_at REAL NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY (sandbox_id) REFERENCES sandboxes(sandbox_id)
                    ON DELETE CASCADE
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS agentbox_lifecycle_claims_expiry_idx
            ON agentbox_lifecycle_claims (expires_at)
            """,
            """
            CREATE TABLE IF NOT EXISTS agentbox_orphan_candidates (
                provider_name TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                sandbox_id TEXT,
                first_seen_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                PRIMARY KEY (provider_name, provider_id)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS agentbox_orphan_candidates_grace_idx
            ON agentbox_orphan_candidates (first_seen_at, last_seen_at)
            """,
        ),
    ),
    (
        4,
        "distributed_provider_allocations",
        (
            """
            CREATE TABLE IF NOT EXISTS agentbox_provider_allocations (
                allocation_id TEXT NOT NULL,
                provider_scope TEXT NOT NULL,
                sandbox_id TEXT NOT NULL,
                owner TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('reserved', 'active')),
                provider_id TEXT,
                expires_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (provider_scope, allocation_id)
            )
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS agentbox_provider_allocations_reservation_idx
            ON agentbox_provider_allocations (provider_scope, sandbox_id)
            WHERE state = 'reserved'
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS agentbox_provider_allocations_id_idx
            ON agentbox_provider_allocations (provider_scope, provider_id)
            WHERE provider_id IS NOT NULL
            """,
            """
            CREATE INDEX IF NOT EXISTS agentbox_provider_allocations_capacity_idx
            ON agentbox_provider_allocations (provider_scope, state, expires_at)
            """,
        ),
    ),
    (
        5,
        "database_authoritative_lifecycle",
        (
            "ALTER TABLE sandboxes ADD COLUMN observed_state TEXT NOT NULL DEFAULT 'starting'",
            "ALTER TABLE sandboxes ADD COLUMN status_json TEXT",
            "ALTER TABLE sandboxes ADD COLUMN endpoint_json TEXT",
            "ALTER TABLE sandboxes ADD COLUMN last_failure TEXT",
            "ALTER TABLE sandboxes ADD COLUMN reconcile_after REAL",
            "ALTER TABLE sessions ADD COLUMN sandbox_generation INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE agentbox_activity_leases ADD COLUMN sandbox_generation INTEGER NOT NULL DEFAULT 0",
            "UPDATE sandboxes SET observed_state = 'suspended' WHERE desired_state = 'suspended'",
            "UPDATE sandboxes SET observed_state = 'deleted' WHERE desired_state = 'deleted'",
            "UPDATE sessions SET sandbox_generation = COALESCE((SELECT observed_generation FROM sandboxes WHERE sandboxes.sandbox_id = sessions.sandbox_id), 0) WHERE sandbox_generation = 0",
            "UPDATE agentbox_activity_leases SET sandbox_generation = COALESCE((SELECT observed_generation FROM sandboxes WHERE sandboxes.sandbox_id = agentbox_activity_leases.sandbox_id), 0) WHERE sandbox_generation = 0",
            "CREATE INDEX IF NOT EXISTS agentbox_sandboxes_reconcile_idx ON sandboxes (reconcile_after)",
        ),
    ),
)


POSTGRES_MIGRATIONS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (
        1,
        "preserve_private_core_schema",
        (
            """
            CREATE TABLE IF NOT EXISTS agentbox_sandboxes (
                sandbox_id text PRIMARY KEY,
                env jsonb NOT NULL DEFAULT '{}'::jsonb,
                idle_since_at timestamptz,
                last_active_at timestamptz NOT NULL DEFAULT now(),
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """,
            """
            ALTER TABLE agentbox_sandboxes ADD COLUMN IF NOT EXISTS
                last_active_at timestamptz NOT NULL DEFAULT now()
            """,
            """
            CREATE TABLE IF NOT EXISTS agentbox_sessions (
                sandbox_id text NOT NULL,
                session_id text NOT NULL,
                cwd text NOT NULL,
                env_keys jsonb NOT NULL DEFAULT '[]'::jsonb,
                last_active_at timestamptz NOT NULL,
                active_operations integer NOT NULL DEFAULT 0
                    CHECK (active_operations >= 0),
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (sandbox_id, session_id)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS agentbox_sessions_idle_idx
            ON agentbox_sessions (last_active_at)
            WHERE active_operations = 0
            """,
        ),
    ),
    (
        2,
        "sandbox_desired_and_observed_state",
        (
            "ALTER TABLE agentbox_sandboxes ADD COLUMN IF NOT EXISTS desired_state text NOT NULL DEFAULT 'present'",
            "ALTER TABLE agentbox_sandboxes ADD COLUMN IF NOT EXISTS desired_generation bigint NOT NULL DEFAULT 1",
            "ALTER TABLE agentbox_sandboxes ADD COLUMN IF NOT EXISTS observed_generation bigint NOT NULL DEFAULT 0",
            "ALTER TABLE agentbox_sandboxes ADD COLUMN IF NOT EXISTS provider_name text",
            "ALTER TABLE agentbox_sandboxes ADD COLUMN IF NOT EXISTS provider_id text",
            "ALTER TABLE agentbox_sandboxes ADD COLUMN IF NOT EXISTS instance_id text",
            "ALTER TABLE agentbox_sandboxes ADD COLUMN IF NOT EXISTS last_observed_at timestamptz",
        ),
    ),
    (
        3,
        "activity_leases_lifecycle_claims_and_orphans",
        (
            """
            CREATE TABLE IF NOT EXISTS agentbox_activity_leases (
                lease_id uuid PRIMARY KEY,
                sandbox_id text NOT NULL REFERENCES agentbox_sandboxes(sandbox_id)
                    ON DELETE CASCADE,
                session_id text,
                operation text NOT NULL,
                owner text NOT NULL,
                expires_at timestamptz NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS agentbox_activity_leases_expiry_idx
            ON agentbox_activity_leases (expires_at)
            """,
            """
            CREATE TABLE IF NOT EXISTS agentbox_lifecycle_claims (
                sandbox_id text PRIMARY KEY REFERENCES agentbox_sandboxes(sandbox_id)
                    ON DELETE CASCADE,
                claim_id uuid NOT NULL UNIQUE,
                operation text NOT NULL,
                owner text NOT NULL,
                expires_at timestamptz NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS agentbox_lifecycle_claims_expiry_idx
            ON agentbox_lifecycle_claims (expires_at)
            """,
            """
            CREATE TABLE IF NOT EXISTS agentbox_orphan_candidates (
                provider_name text NOT NULL,
                provider_id text NOT NULL,
                sandbox_id text,
                first_seen_at timestamptz NOT NULL,
                last_seen_at timestamptz NOT NULL,
                PRIMARY KEY (provider_name, provider_id)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS agentbox_orphan_candidates_grace_idx
            ON agentbox_orphan_candidates (first_seen_at, last_seen_at)
            """,
        ),
    ),
    (
        4,
        "distributed_provider_allocations",
        (
            """
            CREATE TABLE IF NOT EXISTS agentbox_provider_allocations (
                allocation_id text NOT NULL,
                provider_scope text NOT NULL,
                sandbox_id text NOT NULL,
                owner text NOT NULL,
                state text NOT NULL CHECK (state IN ('reserved', 'active')),
                provider_id text,
                expires_at timestamptz,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (provider_scope, allocation_id)
            )
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS agentbox_provider_allocations_reservation_idx
            ON agentbox_provider_allocations (provider_scope, sandbox_id)
            WHERE state = 'reserved'
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS agentbox_provider_allocations_id_idx
            ON agentbox_provider_allocations (provider_scope, provider_id)
            WHERE provider_id IS NOT NULL
            """,
            """
            CREATE INDEX IF NOT EXISTS agentbox_provider_allocations_capacity_idx
            ON agentbox_provider_allocations (provider_scope, state, expires_at)
            """,
        ),
    ),
    (
        5,
        "database_authoritative_lifecycle",
        (
            "ALTER TABLE agentbox_sandboxes ADD COLUMN IF NOT EXISTS observed_state text NOT NULL DEFAULT 'starting'",
            "ALTER TABLE agentbox_sandboxes ADD COLUMN IF NOT EXISTS status_data jsonb",
            "ALTER TABLE agentbox_sandboxes ADD COLUMN IF NOT EXISTS endpoint_data jsonb",
            "ALTER TABLE agentbox_sandboxes ADD COLUMN IF NOT EXISTS last_failure text",
            "ALTER TABLE agentbox_sandboxes ADD COLUMN IF NOT EXISTS reconcile_after timestamptz",
            "ALTER TABLE agentbox_sessions ADD COLUMN IF NOT EXISTS sandbox_generation bigint NOT NULL DEFAULT 0",
            "ALTER TABLE agentbox_activity_leases ADD COLUMN IF NOT EXISTS sandbox_generation bigint NOT NULL DEFAULT 0",
            "UPDATE agentbox_sandboxes SET observed_state = 'suspended' WHERE desired_state = 'suspended'",
            "UPDATE agentbox_sandboxes SET observed_state = 'deleted' WHERE desired_state = 'deleted'",
            "UPDATE agentbox_sessions x SET sandbox_generation = s.observed_generation FROM agentbox_sandboxes s WHERE x.sandbox_id = s.sandbox_id AND x.sandbox_generation = 0",
            "UPDATE agentbox_activity_leases l SET sandbox_generation = s.observed_generation FROM agentbox_sandboxes s WHERE l.sandbox_id = s.sandbox_id AND l.sandbox_generation = 0",
            "CREATE INDEX IF NOT EXISTS agentbox_sandboxes_reconcile_idx ON agentbox_sandboxes (reconcile_after) WHERE reconcile_after IS NOT NULL",
        ),
    ),
)
