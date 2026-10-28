"""Migration 0063 carries every pre-existing connector grant into pod rows.

Seeded before it, in one pod: a `connector.use` grant on the old uuid5 id, and
three pins of a raw account onto an agent -- one its owner made (consented),
one a colleague made on a member's account (not consented), one on the account
of somebody who has left the pod. After it: a PER_MEMBER pod connector carrying
the first grant; a SHARED connector running as the consented account, now a
POD-visible pod account; SHARED connectors with no account for the other two,
the member's account in the pod as PERSONAL and the departed one's nowhere;
the agent holding `connector.use` on each; and no grant on a raw account left.
The downgrade puts the grants back on the shapes they had.

Surfaces and schedules stored an `account_id` too. Each now names a pod
connector by slug: a surface its binder's account, shared POD-wide behind a
SHARED connector (NEEDS_ACCOUNT once the binder has left), a schedule its
owner's own account through the installation's PER_MEMBER connector, or -- an
account somebody else owns -- whatever that owner shared, else a SHARED
connector with no account. The downgrade puts each `account_id` back where the
connector still says which account it meant.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

# A chain walk on its own Postgres, like the other migration tests: the nightly
# protected lane.
pytestmark = [pytest.mark.e2e, pytest.mark.slow]

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0062_usage_counter_basis"
REVISION = "0063_pod_connectors"
LEGACY_GMAIL = uuid5(uuid5(NAMESPACE_URL, "lemma.connector.resource"), "gmail")


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
            "pod",
            "install",
            "owner",
            "consenting",
            "member",
            "departed",
            "agent",
            "consented",
            "unconsented",
            "orphaned",
            "slack_install",
            "bot_own",
            "bot_left",
            "owner_gmail",
            "stray",
            "own_surface",
            "left_surface",
            "pinned_surface",
            "own_schedule",
            "owner_schedule",
            "foreign_schedule",
            "stray_schedule",
        )
    }
    with psycopg.connect(plain_url) as connection:
        run = connection.execute
        run(
            "INSERT INTO organizations (id, name, slug, join_policy, created_at, "
            "updated_at) VALUES (%s, 'Org', %s, 'INVITE_ONLY', now(), now())",
            (ids["org"], f"org-{ids['org'].hex[:8]}"),
        )
        for person in ("owner", "consenting", "member", "departed"):
            run(
                "INSERT INTO users (id, email, is_verified, is_active, "
                "is_superuser, is_deleted, created_at, updated_at) VALUES "
                "(%s, %s, true, true, false, false, now(), now())",
                (ids[person], f"{ids[person].hex[:8]}@example.com"),
            )
            org_member = uuid4()
            run(
                "INSERT INTO organization_members (id, organization_id, user_id, "
                "role, created_at, updated_at) VALUES "
                "(%s, %s, %s, 'ORG_MEMBER', now(), now())",
                (org_member, ids["org"], ids[person]),
            )
            ids[f"{person}_org_member"] = org_member
        run(
            "INSERT INTO pods (id, user_id, organization_id, name, config, "
            "is_deleted, created_at, updated_at) "
            "VALUES (%s, %s, %s, 'Pod', '{}', false, now(), now())",
            (ids["pod"], ids["owner"], ids["org"]),
        )
        # The departed person has no pod membership any more.
        for person in ("owner", "consenting", "member"):
            run(
                "INSERT INTO pod_members (id, pod_id, organization_member_id, "
                "created_at, updated_at) VALUES (%s, %s, %s, now(), now())",
                (uuid4(), ids["pod"], ids[f"{person}_org_member"]),
            )
        run(
            "INSERT INTO connectors (id, title, description, kinds, is_active, "
            "created_at, updated_at) VALUES ('gmail', 'Gmail', 'mail', '[]', "
            "true, now(), now()) ON CONFLICT DO NOTHING"
        )
        run(
            "INSERT INTO auth_configs (id, organization_id, connector_id, name, "
            "kind, config_source, status, is_default, created_at, updated_at) "
            "VALUES (%s, %s, 'gmail', 'gmail', 'http', 'SYSTEM_DEFAULT', "
            "'ACTIVE', true, now(), now())",
            (ids["install"], ids["org"]),
        )
        for account, owner in (
            ("consented", "consenting"),
            ("unconsented", "member"),
            ("orphaned", "departed"),
        ):
            run(
                "INSERT INTO accounts (id, user_id, organization_id, "
                "auth_config_id, connector_id, status, is_default, created_at, "
                "updated_at) VALUES (%s, %s, %s, %s, 'gmail', 'CONNECTED', true, "
                "now(), now())",
                (ids[account], ids[owner], ids["org"], ids["install"]),
            )
        for permission, resource_type in (
            ("connector.use", "connector"),
            ("connector_account.use", "connector_account"),
        ):
            run(
                "INSERT INTO auth_permissions (id, scope, resource_type, "
                "description, system_only, created_at, updated_at) VALUES "
                "(%s, 'POD', %s, 'x', false, now(), now()) ON CONFLICT DO NOTHING",
                (permission, resource_type),
            )

        def grant(resource_type, resource_id, permission, created_by):
            run(
                "INSERT INTO resource_permission_grants (id, pod_id, "
                "resource_type, resource_id, grantee_type, grantee_id, "
                "permission_id, created_by_user_id, created_at) VALUES "
                "(%s, %s, %s, %s, 'AGENT', %s, %s, %s, now())",
                (
                    uuid4(),
                    ids["pod"],
                    resource_type,
                    resource_id,
                    ids["agent"],
                    permission,
                    created_by,
                ),
            )

        grant("connector", LEGACY_GMAIL, "connector.use", ids["owner"])
        grant(
            "connector_account",
            ids["consented"],
            "connector_account.use",
            ids["consenting"],
        )
        grant(
            "connector_account",
            ids["unconsented"],
            "connector_account.use",
            ids["owner"],
        )
        grant(
            "connector_account", ids["orphaned"], "connector_account.use", ids["owner"]
        )
        _seed_surfaces_and_schedules(run, ids)
    return ids


def _seed_surfaces_and_schedules(run, ids: dict[str, UUID]) -> None:
    run(
        "INSERT INTO connectors (id, title, description, kinds, is_active, "
        "created_at, updated_at) VALUES ('slack', 'Slack', 'chat', '[]', "
        "true, now(), now()) ON CONFLICT DO NOTHING"
    )
    run(
        "INSERT INTO auth_configs (id, organization_id, connector_id, name, "
        "kind, config_source, status, is_default, created_at, updated_at) "
        "VALUES (%s, %s, 'slack', 'slack', 'http', 'SYSTEM_DEFAULT', "
        "'ACTIVE', true, now(), now())",
        (ids["slack_install"], ids["org"]),
    )
    for account, owner, install, connector, default in (
        ("bot_own", "member", "slack_install", "slack", True),
        ("bot_left", "departed", "slack_install", "slack", True),
        ("owner_gmail", "owner", "install", "gmail", True),
        ("stray", "consenting", "install", "gmail", False),
    ):
        run(
            "INSERT INTO accounts (id, user_id, organization_id, "
            "auth_config_id, connector_id, status, is_default, created_at, "
            "updated_at) VALUES (%s, %s, %s, %s, %s, 'CONNECTED', %s, "
            "now(), now())",
            (ids[account], ids[owner], ids["org"], ids[install], connector, default),
        )
    # A surface on its binder's own bot; one whose binder has left the pod; one
    # on the account whose owner already pinned it with consent.
    for surface, account in (
        ("own_surface", "bot_own"),
        ("left_surface", "bot_left"),
        ("pinned_surface", "consented"),
    ):
        # One agent each: an agent has one surface per platform.
        agent_id = uuid4()
        run(
            "INSERT INTO agents (id, pod_id, user_id, name, visibility, "
            "instruction, toolsets, created_at, updated_at) VALUES (%s, %s, %s, "
            "%s, 'POD', 'x', '[]', now(), now())",
            (agent_id, ids["pod"], ids["owner"], f"{surface}-agent"),
        )
        run(
            "INSERT INTO agent_surfaces (id, pod_id, organization_id, agent_id, "
            "name, surface_type, config, credential_mode, account_id, "
            "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, 'SLACK', '{}', "
            "'CUSTOM', %s, now(), now())",
            (
                ids[surface],
                ids["pod"],
                ids["org"],
                agent_id,
                surface,
                ids[account],
            ),
        )
    # The owner's schedules on their own accounts; the owner's schedules on a
    # colleague's -- one pinned without consent, one never shared at all.
    for schedule, owner, account in (
        ("own_schedule", "member", "unconsented"),
        ("owner_schedule", "owner", "owner_gmail"),
        ("foreign_schedule", "owner", "unconsented"),
        ("stray_schedule", "owner", "stray"),
    ):
        run(
            "INSERT INTO schedules (id, user_id, pod_id, name, schedule_type, "
            "config, visibility, is_active, is_internal, account_id, created_at, "
            "updated_at) VALUES (%s, %s, %s, %s, 'WEBHOOK', '{}', 'POD', true, "
            "false, %s, now(), now())",
            (ids[schedule], ids[owner], ids["pod"], schedule, ids[account]),
        )


def _state(plain_url: str, pod_id: UUID) -> dict:
    with psycopg.connect(plain_url) as connection:
        run = connection.execute
        connectors = run(
            "SELECT pc.id, pc.slug, pc.mode, pa.account_id, pa.visibility "
            "FROM pod_connectors pc LEFT JOIN pod_accounts pa "
            "ON pa.id = pc.shared_pod_account_id WHERE pc.pod_id = %s",
            (pod_id,),
        ).fetchall()
        pod_accounts = run(
            "SELECT account_id, visibility, source FROM pod_accounts WHERE pod_id = %s",
            (pod_id,),
        ).fetchall()
        grants = run(
            "SELECT resource_type, resource_id, permission_id "
            "FROM resource_permission_grants WHERE pod_id = %s",
            (pod_id,),
        ).fetchall()
        surfaces = run(
            "SELECT id, pod_connector_slug FROM agent_surfaces WHERE pod_id = %s",
            (pod_id,),
        ).fetchall()
        schedules = run(
            "SELECT id, pod_connector_slug FROM schedules WHERE pod_id = %s",
            (pod_id,),
        ).fetchall()
        defaults = run(
            "SELECT account_id FROM pod_accounts WHERE pod_id = %s "
            "AND is_member_default",
            (pod_id,),
        ).fetchall()
    return {
        "connectors": connectors,
        "pod_accounts": {row[0]: (row[1], row[2]) for row in pod_accounts},
        "member_defaults": {row[0] for row in defaults},
        "grants": grants,
        "surfaces": dict(surfaces),
        "schedules": dict(schedules),
    }


def _account_ids(plain_url: str, table: str, pod_id: UUID) -> dict[UUID, UUID | None]:
    with psycopg.connect(plain_url) as connection:
        return dict(
            connection.execute(
                f"SELECT id, account_id FROM {table} WHERE pod_id = %s",  # noqa: S608
                (pod_id,),
            ).fetchall()
        )


def test_every_legacy_connector_grant_becomes_a_pod_row_and_comes_back() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        ids = _seed(plain_url)

        _migrate(database_url, "upgrade", REVISION)
        after = _state(plain_url, ids["pod"])

        by_slug = {row[1]: row for row in after["connectors"]}
        assert by_slug["gmail"][2] == "PER_MEMBER"
        pins = {slug for slug in by_slug if slug.startswith("gmail-shared")} - {
            "gmail-shared-4"
        }
        assert pins == {"gmail-shared", "gmail-shared-2", "gmail-shared-3"}
        # Only the consented pin keeps running, as a POD-visible pod account.
        running = [(by_slug[slug][3], by_slug[slug][4]) for slug in sorted(pins)]
        assert [row for row in running if row[0] is not None] == [
            (ids["consented"], "POD")
        ]
        # A member's unconsented account is theirs alone; the departed one's
        # is in the pod no more. The surface binder's bot is shared POD-wide;
        # the schedule owner's own account is in the pod as theirs alone.
        assert after["pod_accounts"] == {
            ids["consented"]: ("POD", "MIGRATED"),
            ids["unconsented"]: ("PERSONAL", "MIGRATED"),
            ids["bot_own"]: ("POD", "MIGRATED"),
            ids["owner_gmail"]: ("PERSONAL", "MIGRATED"),
        }
        # Nobody has said any of them may go inside a sandbox.
        with psycopg.connect(plain_url) as connection:
            assert connection.execute(
                "SELECT count(*) FROM pod_accounts WHERE allow_sandbox_use"
            ).fetchone() == (0,)
        # The agent holds `connector.use` on the per-member connector and on
        # each pinned one, and no grant names a raw account any more.
        granted = {by_slug[slug][0] for slug in pins | {"gmail"}}
        assert {
            (resource_id, permission)
            for resource_type, resource_id, permission in after["grants"]
        } == {(connector_id, "connector.use") for connector_id in granted}

        # Surfaces: a SHARED connector each, running as the binder's own bot
        # while they are a member, as nothing once they have left, and as the
        # pin its owner already made when there was one.
        surfaces = after["surfaces"]
        own = by_slug[surfaces[ids["own_surface"]]]
        assert own[2:] == ("SHARED", ids["bot_own"], "POD")
        left = by_slug[surfaces[ids["left_surface"]]]
        assert left[2:] == ("SHARED", None, None)
        assert {own[1], left[1]} == {"slack-surface", "slack-surface-2"}
        pinned = by_slug[surfaces[ids["pinned_surface"]]]
        assert pinned[1] in pins and pinned[3:] == (ids["consented"], "POD")

        # Schedules: the owner's own account through the per-member connector,
        # as their member default; a colleague's account through what that
        # colleague shared, which for these two is nothing.
        schedules = after["schedules"]
        assert schedules[ids["own_schedule"]] == "gmail"
        assert schedules[ids["owner_schedule"]] == "gmail"
        assert {ids["unconsented"], ids["owner_gmail"]} <= after["member_defaults"]
        foreign = by_slug[schedules[ids["foreign_schedule"]]]
        assert foreign[1] in pins and foreign[2:] == ("SHARED", None, None)
        stray = by_slug[schedules[ids["stray_schedule"]]]
        assert stray[1:] == ("gmail-shared-4", "SHARED", None, None)

        with psycopg.connect(plain_url) as connection:
            assert connection.execute(
                "SELECT count(*) FROM information_schema.columns WHERE "
                "column_name = 'account_id' AND table_name IN "
                "('agent_surfaces', 'schedules')"
            ).fetchone() == (0,)

        with psycopg.connect(plain_url) as connection:
            removed = connection.execute(
                "SELECT count(*) FROM auth_permissions WHERE id IN "
                "('connector.manage', 'connector_auth_config.manage', "
                "'connector_account.manage')"
            ).fetchone()
            added = connection.execute(
                "SELECT count(*) FROM auth_permissions WHERE id IN "
                "('org.installation.manage', 'pod.connector.manage', "
                "'connector_account.share')"
            ).fetchone()
        assert removed == (0,) and added == (3,)

        _migrate(database_url, "downgrade", BEFORE)
        with psycopg.connect(plain_url) as connection:
            assert connection.execute(
                "SELECT to_regclass('pod_connectors'), to_regclass('pod_accounts')"
            ).fetchone() == (None, None)
            grants = set(
                connection.execute(
                    "SELECT resource_type, resource_id, permission_id "
                    "FROM resource_permission_grants WHERE pod_id = %s",
                    (ids["pod"],),
                ).fetchall()
            )
        # The per-member grant is back on the uuid5 id, and the pin that ran
        # is back on its account. The two the upgrade stopped stay stopped.
        assert grants == {
            ("connector", LEGACY_GMAIL, "connector.use"),
            ("connector_account", ids["consented"], "connector_account.use"),
        }
        # Every surface and schedule whose connector still names an account is
        # back on it; the ones the upgrade stopped stay stopped.
        assert _account_ids(plain_url, "agent_surfaces", ids["pod"]) == {
            ids["own_surface"]: ids["bot_own"],
            ids["left_surface"]: None,
            ids["pinned_surface"]: ids["consented"],
        }
        assert _account_ids(plain_url, "schedules", ids["pod"]) == {
            ids["own_schedule"]: ids["unconsented"],
            ids["owner_schedule"]: ids["owner_gmail"],
            ids["foreign_schedule"]: None,
            ids["stray_schedule"]: None,
        }

        _migrate(database_url, "upgrade", REVISION)
