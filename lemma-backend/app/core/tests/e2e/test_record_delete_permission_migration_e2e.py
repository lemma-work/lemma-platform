"""Migration 0063, its last three steps: record deletes become their own
permission, Lemma-made Slack apps are marked as such on the surfaces bound
before the flag existed, and making a public link to a file becomes its own
permission, `folder.share`, with every existing link kept (audit M22).

Seeded before it, in one pod: the system role POD_USER and a custom role, both
holding `datastore.record.write`, and that permission granted on a table to a
member, a role, an agent and another pod; then two Slack surfaces bound to a
member's account, one connected through an install Lemma made (named after the
app id) and one through an install a person brought (named after the client
id). After it: both roles and every grant but the pod's carry
`datastore.record.delete` too, and only the Lemma-made surface -- now on a pod
connector -- has its installation say `app_made_by_lemma`. Then POD_USER holding `folder.read`, a
custom role holding only `folder.write`, and `folder.read` granted on a folder
to a member, a role, an agent, a function and another pod; and two public links
to a file, one a person minted and one an agent minted for them. After it: both
roles, and only the member's and the role's grants, carry `folder.share`; both
links are still there, and so is `minted_by_workload`. The downgrade takes all
three back and leaves both links alone.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

# A chain walk on its own Postgres, like the other migration tests: the nightly
# protected lane.
pytestmark = [pytest.mark.e2e, pytest.mark.slow]

BACKEND = Path(__file__).resolve().parents[4]
BEFORE = "0062_usage_counter_basis"
REVISION = "0063_pod_connectors"
WRITE = "datastore.record.write"
DELETE = "datastore.record.delete"
GRANTEES = ("POD_MEMBER", "ROLE", "AGENT", "POD")
READ_FILES = "folder.read"
WRITE_FILES = "folder.write"
SHARE = "folder.share"
FILE_GRANTEES = ("POD_MEMBER", "ROLE", "AGENT", "FUNCTION", "POD")


def _migrate(database_url: str, direction: str, revision: str) -> None:
    result = subprocess.run(
        ["uv", "run", "--no-sync", "alembic", direction, revision],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]


def _seed(plain_url: str) -> dict[str, UUID]:
    ids = {
        name: uuid4()
        for name in (
            "org",
            "user",
            "pod",
            "lemma_agent",
            "own_agent",
            "table",
            "system_role",
            "custom_role",
            "file_role",
            "folder",
            "lemma_install",
            "own_install",
            "lemma_account",
            "own_account",
            "lemma_surface",
            "own_surface",
            "person_link",
            "agent_link",
        )
    }
    with psycopg.connect(plain_url) as connection:
        run = connection.execute
        run(
            "INSERT INTO organizations (id, name, slug, join_policy, created_at, "
            "updated_at) VALUES (%s, 'Org', %s, 'INVITE_ONLY', now(), now())",
            (ids["org"], f"org-{ids['org'].hex[:8]}"),
        )
        run(
            "INSERT INTO users (id, email, is_verified, is_active, is_superuser, "
            "is_deleted, created_at, updated_at) VALUES "
            "(%s, %s, true, true, false, false, now(), now())",
            (ids["user"], f"{ids['user'].hex[:8]}@example.com"),
        )
        run(
            "INSERT INTO pods (id, user_id, organization_id, name, config, "
            "is_deleted, created_at, updated_at) "
            "VALUES (%s, %s, %s, 'Pod', '{}', false, now(), now())",
            (ids["pod"], ids["user"], ids["org"]),
        )
        # A pod member: the surfaces are bound to their accounts.
        org_member = uuid4()
        run(
            "INSERT INTO organization_members (id, organization_id, user_id, "
            "role, created_at, updated_at) VALUES "
            "(%s, %s, %s, 'ORG_MEMBER', now(), now())",
            (org_member, ids["org"], ids["user"]),
        )
        run(
            "INSERT INTO pod_members (id, pod_id, organization_member_id, "
            "created_at, updated_at) VALUES (%s, %s, %s, now(), now())",
            (uuid4(), ids["pod"], org_member),
        )
        # Each surface has its own agent: one Slack surface per agent.
        for agent in ("lemma_agent", "own_agent"):
            run(
                "INSERT INTO agents (id, pod_id, user_id, name, kind, visibility, "
                "instruction, toolsets, created_at, updated_at) VALUES "
                "(%s, %s, %s, %s, 'USER', 'POD', 'Help.', '[]', now(), now())",
                (ids[agent], ids["pod"], ids["user"], agent),
            )
        run(
            "INSERT INTO auth_permissions (id, scope, resource_type, description, "
            "system_only, created_at, updated_at) VALUES (%s, 'POD', "
            "'datastore_record', 'x', false, now(), now()) ON CONFLICT DO NOTHING",
            (WRITE,),
        )
        for role, name, is_system in (
            ("system_role", "POD_USER", True),
            ("custom_role", "RECORD_KEEPER", False),
        ):
            run(
                "INSERT INTO roles (id, organization_id, pod_id, name, is_system, "
                "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, now(), now())",
                (ids[role], ids["org"], ids["pod"], name, is_system),
            )
            run(
                "INSERT INTO role_permissions (id, role_id, permission_id, "
                "created_at) VALUES (%s, %s, %s, now())",
                (uuid4(), ids[role], WRITE),
            )
        for permission, resource_type in (
            (READ_FILES, "folder"),
            (WRITE_FILES, "folder"),
        ):
            run(
                "INSERT INTO auth_permissions (id, scope, resource_type, "
                "description, system_only, created_at, updated_at) VALUES "
                "(%s, 'POD', %s, 'x', false, now(), now()) ON CONFLICT DO NOTHING",
                (permission, resource_type),
            )
        run(
            "INSERT INTO role_permissions (id, role_id, permission_id, "
            "created_at) VALUES (%s, %s, %s, now())",
            (uuid4(), ids["system_role"], READ_FILES),
        )
        # A custom role that writes files reads them too (write implies read),
        # so it shared links before and keeps doing so.
        run(
            "INSERT INTO roles (id, organization_id, pod_id, name, is_system, "
            "created_at, updated_at) VALUES (%s, %s, %s, 'FILE_KEEPER', false, "
            "now(), now())",
            (ids["file_role"], ids["org"], ids["pod"]),
        )
        run(
            "INSERT INTO role_permissions (id, role_id, permission_id, "
            "created_at) VALUES (%s, %s, %s, now())",
            (uuid4(), ids["file_role"], WRITE_FILES),
        )
        for grantee in FILE_GRANTEES:
            run(
                "INSERT INTO resource_permission_grants (id, pod_id, "
                "resource_type, resource_id, grantee_type, grantee_id, "
                "permission_id, created_by_user_id, created_at) VALUES "
                "(%s, %s, 'folder', %s, %s, %s, %s, %s, now())",
                (
                    uuid4(),
                    ids["pod"],
                    ids["folder"],
                    grantee,
                    uuid4(),
                    READ_FILES,
                    ids["user"],
                ),
            )
        for grantee in GRANTEES:
            run(
                "INSERT INTO resource_permission_grants (id, pod_id, "
                "resource_type, resource_id, grantee_type, grantee_id, "
                "permission_id, created_by_user_id, created_at) VALUES "
                "(%s, %s, 'datastore_table', %s, %s, %s, %s, %s, now())",
                (
                    uuid4(),
                    ids["pod"],
                    ids["table"],
                    grantee,
                    uuid4(),
                    WRITE,
                    ids["user"],
                ),
            )

        run(
            "INSERT INTO connectors (id, title, description, kinds, is_active, "
            "created_at, updated_at) VALUES ('slack', 'Slack', 'chat', '[]', "
            "true, now(), now()) ON CONFLICT DO NOTHING"
        )
        # The two names the two code paths write: the app id for an app Lemma
        # made, the client id for one a person brought.
        for kind, name in (
            ("lemma", "Helper (A0ABCDEF12)"),
            ("own", "helper (1234567.8901234)"),
        ):
            install, account = f"{kind}_install", f"{kind}_account"
            surface, agent = f"{kind}_surface", f"{kind}_agent"
            run(
                "INSERT INTO auth_configs (id, organization_id, connector_id, name, "
                "kind, config_source, status, is_default, created_at, updated_at) "
                "VALUES (%s, %s, 'slack', %s, 'http', 'ORG_CUSTOM', 'ACTIVE', "
                "false, now(), now())",
                (ids[install], ids["org"], name),
            )
            run(
                "INSERT INTO accounts (id, user_id, organization_id, "
                "auth_config_id, connector_id, status, is_default, created_at, "
                "updated_at) VALUES (%s, %s, %s, %s, 'slack', 'CONNECTED', false, "
                "now(), now())",
                (ids[account], ids["user"], ids["org"], ids[install]),
            )
            run(
                "INSERT INTO agent_surfaces (id, pod_id, organization_id, agent_id, "
                "name, surface_type, event_mode, credential_mode, config, "
                "account_id, status, created_at, updated_at) VALUES "
                "(%s, %s, %s, %s, %s, 'SLACK', 'WEBHOOK', 'CUSTOM', %s, %s, "
                "'ACTIVE', now(), now())",
                (
                    ids[surface],
                    ids["pod"],
                    ids["org"],
                    ids[agent],
                    surface,
                    json.dumps({"slack": {"app_name": "crm"}}),
                    ids[account],
                ),
            )

        for link, workload in (
            ("person_link", None),
            ("agent_link", f"agent:{ids['own_agent']}"),
        ):
            run(
                "INSERT INTO datastore_signed_links (id, code, pod_id, "
                "created_by_user_id, minted_by_workload, path, object_key, "
                "content_type, filename, max_hits, expires_at, created_at, "
                "updated_at) VALUES (%s, %s, %s, %s, %s, '/notes.md', 'k', "
                "'text/markdown', 'notes.md', 5, now() + interval '1 day', "
                "now(), now())",
                (ids[link], link, ids["pod"], ids["user"], workload),
            )
    return ids


def _state(plain_url: str, ids: dict[str, UUID]) -> dict:
    with psycopg.connect(plain_url) as connection:
        run = connection.execute
        roles = run(
            "SELECT r.name FROM role_permissions rp JOIN roles r ON r.id = "
            "rp.role_id WHERE rp.permission_id = %s AND r.organization_id = %s",
            (DELETE, ids["org"]),
        ).fetchall()
        grants = run(
            "SELECT grantee_type FROM resource_permission_grants "
            "WHERE pod_id = %s AND permission_id = %s",
            (ids["pod"], DELETE),
        ).fetchall()
        share_roles = run(
            "SELECT r.name FROM role_permissions rp JOIN roles r ON r.id = "
            "rp.role_id WHERE rp.permission_id = %s AND r.organization_id = %s",
            (SHARE, ids["org"]),
        ).fetchall()
        share_grants = run(
            "SELECT grantee_type, resource_type, resource_id FROM "
            "resource_permission_grants WHERE pod_id = %s AND permission_id = %s",
            (ids["pod"], SHARE),
        ).fetchall()
        surfaces = run(
            "SELECT id, config FROM agent_surfaces WHERE pod_id = %s", (ids["pod"],)
        ).fetchall()
        installs = run(
            "SELECT id, metadata FROM auth_configs WHERE id = ANY(%s)",
            ([ids["lemma_install"], ids["own_install"]],),
        ).fetchall()
        links = run(
            "SELECT code FROM datastore_signed_links WHERE pod_id = %s",
            (ids["pod"],),
        ).fetchall()
        link_columns = run(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'datastore_signed_links'"
        ).fetchall()
    return {
        "roles": {row[0] for row in roles},
        "grants": sorted(row[0] for row in grants),
        "share_roles": {row[0] for row in share_roles},
        "share_grants": sorted(tuple(row) for row in share_grants),
        "surfaces": {row[0]: row[1] for row in surfaces},
        "made_by_lemma": {
            row[0]: bool((row[1] or {}).get("app_made_by_lemma")) for row in installs
        },
        "links": sorted(row[0] for row in links),
        "workload_column": ("minted_by_workload",) in link_columns,
    }


def test_record_deletes_lemma_made_apps_and_sharing_files() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        ids = _seed(plain_url)

        _migrate(database_url, "upgrade", REVISION)
        after = _state(plain_url, ids)

        assert after["roles"] == {"POD_USER", "RECORD_KEEPER"}
        # Everyone who could delete through the write still can; another pod
        # never could, and is not given it.
        assert after["grants"] == ["AGENT", "POD_MEMBER", "ROLE"]
        # Whether Lemma made the app is said by its installation, not by a
        # surface that would outlive it.
        assert after["surfaces"][ids["lemma_surface"]] == {"slack": {"app_name": "crm"}}
        assert after["surfaces"][ids["own_surface"]] == {"slack": {"app_name": "crm"}}
        assert after["made_by_lemma"] == {
            ids["lemma_install"]: True,
            ids["own_install"]: False,
        }
        # Whoever shared files before still does; software only by a grant,
        # and none it held is turned into one.
        assert after["share_roles"] == {"POD_USER", "FILE_KEEPER"}
        assert after["share_grants"] == [
            ("POD_MEMBER", "folder", ids["folder"]),
            ("ROLE", "folder", ids["folder"]),
        ]
        # No link is revoked, an agent's included: each is still re-checked,
        # as whoever minted it, on every fetch.
        assert after["links"] == ["agent_link", "person_link"]
        assert after["workload_column"] is True

        _migrate(database_url, "downgrade", BEFORE)
        before = _state(plain_url, ids)
        with psycopg.connect(plain_url) as connection:
            gone = connection.execute(
                "SELECT count(*) FROM auth_permissions WHERE id = ANY(%s)",
                ([DELETE, SHARE],),
            ).fetchone()
        assert gone == (0,)
        assert before["roles"] == set() and before["grants"] == []
        assert before["share_roles"] == set() and before["share_grants"] == []
        assert all(
            config == {"slack": {"app_name": "crm"}}
            for config in before["surfaces"].values()
        )
        assert not any(before["made_by_lemma"].values())
        assert before["workload_column"] is True
        assert before["links"] == ["agent_link", "person_link"]

        # And again, from where the downgrade left it.
        _migrate(database_url, "upgrade", REVISION)
        assert _state(plain_url, ids) == after
