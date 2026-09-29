"""0042 backfills each verified identity's proof from what the row holds."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0041_conversation_last_activity"
AFTER = "0042_surface_identity_proof"


def test_existing_identities_keep_the_reading_they_had_and_the_column_rolls_back() -> (
    None
):
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        environment = {**os.environ, "DATABASE_URL": database_url}
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")

        def migrate(direction: str, revision: str) -> None:
            result = subprocess.run(
                ["uv", "run", "--no-sync", "alembic", direction, revision],
                cwd=BACKEND,
                env=environment,
                capture_output=True,
                text=True,
                timeout=180,
            )
            assert result.returncode == 0, result.stdout + result.stderr

        migrate("upgrade", BEFORE)
        with psycopg.connect(plain_url) as connection:
            # The user rows these point at are beside the point of a backfill.
            connection.execute("SET session_replication_role = replica")
            for actor, phone in (("with-phone", "+15550001111"), ("no-phone", None)):
                connection.execute(
                    "INSERT INTO surface_verified_identities (id, created_at, "
                    "updated_at, binding_key, platform, tenant_id, external_user_id, "
                    "user_id, verified_phone) VALUES (%s, now(), now(), %s, "
                    "'TELEGRAM', '', %s, %s, %s)",
                    (uuid4(), uuid4().hex, actor, uuid4(), phone),
                )

        migrate("upgrade", AFTER)
        with psycopg.connect(plain_url) as connection:
            proofs = dict(
                connection.execute(
                    "SELECT external_user_id, proof FROM surface_verified_identities"
                ).fetchall()
            )
            assert proofs == {"with-phone": "phone", "no-phone": "email"}
            with pytest.raises(psycopg.errors.CheckViolation):
                connection.execute(
                    "UPDATE surface_verified_identities SET proof = 'guess'"
                )

        migrate("downgrade", BEFORE)
        with psycopg.connect(plain_url) as connection:
            columns = {
                row[0]
                for row in connection.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'surface_verified_identities'"
                ).fetchall()
            }
            assert "proof" not in columns
