"""Pod connectors and pod accounts: what a pod uses, as rows of its own.

Two tables, `pod_connectors` and `pod_accounts`, referencing the org's
`auth_configs` and `accounts`, which are unchanged. Their DDL is the models'
exactly (`connectors/infrastructure/models/pod_connector.py`).

The backfill turns every pre-existing grant shape into the new model, and
stops whatever it cannot carry over with consent rather than keeping it
working (decision 3 of the design):

1. A `connector.use` grant was keyed on `uuid5(<connector id>)`. Per pod, each
   becomes a PER_MEMBER pod connector `<connector id>` on the org's default
   ACTIVE installation of it, and the grant is rewritten onto that row. With no
   installation to point at, the grant is dropped.
2. A `connector_account.use` grant pinned somebody's raw account. Per (pod,
   account):
   - consented -- some grant on it was created by the account's owner, who is
     still a pod member: the owner's pod account becomes POD-visible
     (RESTRICTED, keeping the people grants, when there were any), and a
     SHARED pod connector `<connector id>-shared[-n]` runs as it;
   - otherwise the SHARED connector is created with no account
     (NEEDS_ACCOUNT), and the owner, if still a member, gets their account in
     the pod as PERSONAL -- theirs to share or not.
   Either way each agent or function grant becomes `connector.use` on the new
   connector, and every grant on the raw account is deleted.
3. There is no usage table to seed pod accounts from: operation counts go to
   product analytics, not the database. Pod accounts appear on first use.
4. A surface stored the `account_id` its binder chose -- their own, checked at
   bind -- so it is consented. Its binder still a pod member: their pod account
   becomes POD-visible and a SHARED connector runs as it (the one already
   pointing at it, else `<connector id>-surface[-n]`). Otherwise the SHARED
   connector has no account (NEEDS_ACCOUNT), one per (pod, account). Either way
   the surface names it in `pod_connector_slug`.
5. A schedule stored an `account_id` too. The schedule owner's own account,
   while they are a pod member: their PERSONAL pod account (their member
   default when they have none) and the installation's PER_MEMBER connector,
   which the owner's fires resolve to it. Somebody else's account (a bundle
   applied it, audit M14): whatever SHARED connector its owner consented to,
   else a SHARED one with no account. `schedules.pod_connector_slug` names it.
   Then both `account_id` columns go.

Permissions: `org.installation.manage`, `pod.connector.manage` and
`connector_account.share` are added (the first two to the system roles that
carry them); `connector.manage`, `connector_auth_config.manage` and
`connector_account.manage` -- never enforced -- are deleted, which cascades
their role rows and grants.

The downgrade puts each surface's and schedule's `account_id` back from the
account its connector still resolves to (nothing, where the upgrade stopped
it), puts grants back on the shapes they had, restores the deleted permissions
to their roles, and drops the tables.

Revision ID: 0063_pod_connectors
Revises: 0062_usage_counter_basis
"""

from __future__ import annotations

import re
from collections import defaultdict
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import sqlalchemy as sa
from alembic import op

revision = "0063_pod_connectors"
down_revision = "0062_usage_counter_basis"
branch_labels = None
depends_on = None

_NEW_TABLES = """
CREATE TABLE pod_accounts (
	pod_id UUID NOT NULL,
	account_id UUID NOT NULL,
	auth_config_id UUID NOT NULL,
	owner_user_id UUID NOT NULL,
	visibility VARCHAR(20) DEFAULT 'PERSONAL' NOT NULL,
	is_member_default BOOLEAN DEFAULT 'false' NOT NULL,
	source VARCHAR(20) NOT NULL,
	last_used_at TIMESTAMP WITH TIME ZONE,
	allow_sandbox_use BOOLEAN DEFAULT 'false' NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_pod_accounts_pod_account UNIQUE (pod_id, account_id),
	FOREIGN KEY(pod_id) REFERENCES pods (id) ON DELETE CASCADE,
	FOREIGN KEY(account_id) REFERENCES accounts (id) ON DELETE CASCADE,
	FOREIGN KEY(auth_config_id) REFERENCES auth_configs (id) ON DELETE CASCADE,
	FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_pod_accounts_account ON pod_accounts (account_id);
CREATE INDEX ix_pod_accounts_pod_owner ON pod_accounts (pod_id, owner_user_id);
CREATE UNIQUE INDEX uq_pod_accounts_member_default ON pod_accounts (pod_id, owner_user_id, auth_config_id) WHERE is_member_default;
CREATE TABLE pod_connectors (
	pod_id UUID NOT NULL,
	slug VARCHAR(63) NOT NULL,
	auth_config_id UUID NOT NULL,
	mode VARCHAR(20) NOT NULL,
	shared_pod_account_id UUID,
	required BOOLEAN DEFAULT 'false' NOT NULL,
	purpose TEXT,
	visibility VARCHAR(20) DEFAULT 'POD' NOT NULL,
	source VARCHAR(20) DEFAULT 'MANUAL' NOT NULL,
	created_by_user_id UUID,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_pod_connectors_pod_slug UNIQUE (pod_id, slug),
	FOREIGN KEY(pod_id) REFERENCES pods (id) ON DELETE CASCADE,
	FOREIGN KEY(auth_config_id) REFERENCES auth_configs (id) ON DELETE CASCADE,
	FOREIGN KEY(shared_pod_account_id) REFERENCES pod_accounts (id) ON DELETE SET NULL,
	FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);
CREATE INDEX ix_pod_connectors_auth_config ON pod_connectors (auth_config_id);
CREATE INDEX ix_pod_connectors_shared_pod_account ON pod_connectors (shared_pod_account_id);
"""

#: (id, scope, resource_type, description, system roles that hold it)
_ADDED = (
    (
        "org.installation.manage",
        "ORG",
        "installation",
        "Manage the organization's connector installations",
        ("ORG_EDITOR", "ORG_OWNER"),
    ),
    (
        "pod.connector.manage",
        "POD",
        "pod",
        "Declare and change the pod's connectors",
        ("POD_EDITOR", "POD_ADMIN", "ORG_OWNER"),
    ),
    (
        "connector_account.share",
        "POD",
        "connector_account",
        "Share or withdraw one's own account in a pod (owner only)",
        (),
    ),
)
#: The same shape, for what is deleted, so the downgrade can put it back.
_REMOVED = (
    (
        "connector.manage",
        "ORG",
        "connector",
        "Enable and configure organization connectors",
        ("ORG_EDITOR", "ORG_OWNER"),
    ),
    (
        "connector_auth_config.manage",
        "ORG",
        "connector_auth_config",
        "Manage organization connector auth configs",
        ("ORG_EDITOR", "ORG_OWNER"),
    ),
    (
        "connector_account.manage",
        "POD",
        "connector_account",
        "Manage connected connector accounts",
        ("POD_ADMIN", "ORG_OWNER"),
    ),
)

_SLUG_COLUMNS = (
    "ALTER TABLE agent_surfaces ADD COLUMN pod_connector_slug VARCHAR(63)",
    (
        "CREATE INDEX ix_agent_surfaces_pod_connector ON agent_surfaces "
        "(pod_id, pod_connector_slug)"
    ),
    "ALTER TABLE schedules ADD COLUMN pod_connector_slug VARCHAR(63)",
    "CREATE INDEX ix_schedules_pod_connector ON schedules (pod_id, pod_connector_slug)",
)
#: The pre-0063 shape exactly: the indexes the baseline made, the FKs as 0027
#: left them (SET NULL on both).
_ACCOUNT_COLUMNS = (
    "ALTER TABLE agent_surfaces ADD COLUMN account_id UUID",
    (
        "ALTER TABLE agent_surfaces ADD CONSTRAINT agent_surfaces_account_id_fkey "
        "FOREIGN KEY (account_id) REFERENCES accounts (id) ON DELETE SET NULL"
    ),
    "CREATE INDEX ix_agent_surfaces_account_id ON agent_surfaces (account_id)",
    "ALTER TABLE schedules ADD COLUMN account_id UUID",
    (
        "ALTER TABLE schedules ADD CONSTRAINT schedules_account_id_fkey "
        "FOREIGN KEY (account_id) REFERENCES accounts (id) ON DELETE SET NULL"
    ),
    "CREATE INDEX ix_schedules_account ON schedules (account_id)",
    "CREATE INDEX ix_schedules_account_id ON schedules (account_id)",
)

_WORKLOADS = ("AGENT", "FUNCTION")
_PEOPLE = ("ROLE", "POD_MEMBER")
#: What `connector_resource_id` computed before this revision deleted it.
_CONNECTOR_NAMESPACE = uuid5(NAMESPACE_URL, "lemma.connector.resource")


def _log():
    """Where this migration says what it dropped, collapsed or left unbound."""
    import logging

    return logging.getLogger("alembic.runtime.migration")


def _legacy_connector_resource_id(connector_id: str) -> UUID:
    return uuid5(_CONNECTOR_NAMESPACE, connector_id)


def _slug_base(connector_id: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", connector_id.lower()).strip("-_")
    return (slug or "connector")[:50]


def _free_slug(conn, pod_id: UUID, base: str) -> str:
    taken = set(
        conn.execute(
            sa.text("SELECT slug FROM pod_connectors WHERE pod_id = :pod"),
            {"pod": pod_id},
        ).scalars()
    )
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _insert_pod_connector(
    conn,
    *,
    pod_id: UUID,
    slug: str,
    auth_config_id: UUID,
    mode: str,
    shared_pod_account_id: UUID | None,
    created_by: UUID | None,
) -> UUID:
    pod_connector_id = uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO pod_connectors (id, pod_id, slug, auth_config_id, mode, "
            "shared_pod_account_id, required, visibility, created_by_user_id, "
            "created_at, updated_at) VALUES (:id, :pod, :slug, :ac, :mode, :pa, "
            "false, 'POD', :by, now(), now())"
        ),
        {
            "id": pod_connector_id,
            "pod": pod_id,
            "slug": slug,
            "ac": auth_config_id,
            "mode": mode,
            "pa": shared_pod_account_id,
            "by": created_by,
        },
    )
    return pod_connector_id


def _per_member_connectors(conn) -> None:
    by_legacy_id = {
        _legacy_connector_resource_id(connector_id): connector_id
        for connector_id in conn.execute(sa.text("SELECT id FROM connectors")).scalars()
    }
    targets = conn.execute(
        sa.text(
            "SELECT g.pod_id, g.resource_id, p.organization_id, "
            "(array_agg(g.created_by_user_id ORDER BY g.created_at))[1] "
            "FROM resource_permission_grants g JOIN pods p ON p.id = g.pod_id "
            "WHERE g.resource_type = 'connector' "
            "GROUP BY g.pod_id, g.resource_id, p.organization_id"
        )
    ).all()
    for pod_id, legacy_id, organization_id, created_by in targets:
        connector_id = by_legacy_id.get(legacy_id)
        install = (
            conn.execute(
                sa.text(
                    "SELECT id FROM auth_configs WHERE organization_id = :org "
                    "AND connector_id = :c AND status = 'ACTIVE' "
                    "ORDER BY is_default DESC, created_at LIMIT 1"
                ),
                {"org": organization_id, "c": connector_id},
            ).scalar()
            if connector_id is not None
            else None
        )
        if install is None:
            dropped = conn.execute(
                sa.text(
                    "DELETE FROM resource_permission_grants WHERE pod_id = :pod "
                    "AND resource_type = 'connector' AND resource_id = :r"
                ),
                {"pod": pod_id, "r": legacy_id},
            ).rowcount
            _log().warning(
                "0063: pod %s: dropped %d connector grant(s) for %s -- the org has "
                "no ACTIVE installation of it, so nothing could be declared.",
                pod_id,
                dropped,
                connector_id or legacy_id,
            )
            continue
        installs = conn.execute(
            sa.text(
                "SELECT count(*) FROM auth_configs WHERE organization_id = :org "
                "AND connector_id = :c AND status = 'ACTIVE'"
            ),
            {"org": organization_id, "c": connector_id},
        ).scalar()
        if installs and installs > 1:
            _log().info(
                "0063: pod %s: %s has %d ACTIVE installations; its grants were "
                "declared on the default one (%s).",
                pod_id,
                connector_id,
                installs,
                install,
            )
        pod_connector_id = _insert_pod_connector(
            conn,
            pod_id=pod_id,
            slug=_free_slug(conn, pod_id, _slug_base(connector_id)),
            auth_config_id=install,
            mode="PER_MEMBER",
            shared_pod_account_id=None,
            created_by=created_by,
        )
        conn.execute(
            sa.text(
                "UPDATE resource_permission_grants SET resource_id = :new "
                "WHERE pod_id = :pod AND resource_type = 'connector' "
                "AND resource_id = :old"
            ),
            {"new": pod_connector_id, "pod": pod_id, "old": legacy_id},
        )


def _is_pod_member(conn, pod_id: UUID, user_id: UUID) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM pod_members pm JOIN organization_members om "
                "ON om.id = pm.organization_member_id "
                "WHERE pm.pod_id = :pod AND om.user_id = :user"
            ),
            {"pod": pod_id, "user": user_id},
        ).scalar()
    )


def _add_pod_account(conn, *, pod_id: UUID, account, visibility: str) -> UUID:
    has_default = conn.execute(
        sa.text(
            "SELECT 1 FROM pod_accounts WHERE pod_id = :pod AND owner_user_id = :u "
            "AND auth_config_id = :ac AND is_member_default"
        ),
        {"pod": pod_id, "u": account.user_id, "ac": account.auth_config_id},
    ).scalar()
    pod_account_id = uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO pod_accounts (id, pod_id, account_id, auth_config_id, "
            "owner_user_id, visibility, is_member_default, source, created_at, "
            "updated_at) VALUES (:id, :pod, :a, :ac, :u, :vis, :dflt, 'MIGRATED', "
            "now(), now())"
        ),
        {
            "id": pod_account_id,
            "pod": pod_id,
            "a": account.id,
            "ac": account.auth_config_id,
            "u": account.user_id,
            "vis": visibility,
            "dflt": not has_default,
        },
    )
    return pod_account_id


def _pinned_accounts(conn, unbacked: dict) -> None:
    grants = conn.execute(
        sa.text(
            "SELECT g.pod_id, g.resource_id, g.grantee_type, g.grantee_id, "
            "g.permission_id, g.created_by_user_id, p.organization_id "
            "FROM resource_permission_grants g JOIN pods p ON p.id = g.pod_id "
            "WHERE g.resource_type = 'connector_account'"
        )
    ).all()
    pins: dict[tuple[UUID, UUID], list] = defaultdict(list)
    for grant in grants:
        pins[(grant.pod_id, grant.resource_id)].append(grant)
    for (pod_id, account_id), pin in pins.items():
        _carry_over_pin(conn, pod_id, account_id, pin, unbacked)


def _carry_over_pin(
    conn, pod_id: UUID, account_id: UUID, pin: list, unbacked: dict
) -> None:
    account = conn.execute(
        sa.text(
            "SELECT id, user_id, auth_config_id, connector_id, organization_id "
            "FROM accounts WHERE id = :a"
        ),
        {"a": account_id},
    ).first()
    if account is not None and account.organization_id == pin[0].organization_id:
        member = _is_pod_member(conn, pod_id, account.user_id)
        consented = member and any(g.created_by_user_id == account.user_id for g in pin)
        people = [
            g
            for g in pin
            if g.grantee_type in _PEOPLE and g.permission_id == "connector_account.use"
        ]
        pod_account_id = None
        if member:
            visibility = (
                ("RESTRICTED" if people else "POD") if consented else "PERSONAL"
            )
            pod_account_id = _add_pod_account(
                conn, pod_id=pod_id, account=account, visibility=visibility
            )
        slug = _free_slug(conn, pod_id, f"{_slug_base(account.connector_id)}-shared")
        pod_connector_id = _insert_pod_connector(
            conn,
            pod_id=pod_id,
            slug=slug,
            auth_config_id=account.auth_config_id,
            mode="SHARED",
            shared_pod_account_id=pod_account_id if consented else None,
            created_by=account.user_id if consented else pin[0].created_by_user_id,
        )
        if not consented:
            unbacked[(pod_id, account_id)] = slug
        for grantee in {
            (g.grantee_type, g.grantee_id, g.created_by_user_id)
            for g in pin
            if g.grantee_type in _WORKLOADS
        }:
            conn.execute(
                sa.text(
                    "INSERT INTO resource_permission_grants (id, pod_id, "
                    "resource_type, resource_id, grantee_type, grantee_id, "
                    "permission_id, created_by_user_id, created_at) VALUES (:id, "
                    ":pod, 'connector', :pc, :gt, :gid, 'connector.use', :by, now()) "
                    "ON CONFLICT ON CONSTRAINT uq_resource_permission_grants_scope "
                    "DO NOTHING"
                ),
                {
                    "id": uuid4(),
                    "pod": pod_id,
                    "pc": pod_connector_id,
                    "gt": grantee[0],
                    "gid": grantee[1],
                    "by": grantee[2],
                },
            )
        if consented and people:
            conn.execute(
                sa.text(
                    "UPDATE resource_permission_grants SET resource_id = :pa "
                    "WHERE pod_id = :pod AND resource_type = 'connector_account' "
                    "AND resource_id = :a AND grantee_type IN ('ROLE', 'POD_MEMBER') "
                    "AND permission_id = 'connector_account.use'"
                ),
                {"pa": pod_account_id, "pod": pod_id, "a": account_id},
            )
    conn.execute(
        sa.text(
            "DELETE FROM resource_permission_grants WHERE pod_id = :pod "
            "AND resource_type = 'connector_account' AND resource_id = :a"
        ),
        {"pod": pod_id, "a": account_id},
    )


def _account(conn, account_id: UUID):
    return conn.execute(
        sa.text(
            "SELECT id, user_id, auth_config_id, connector_id, organization_id "
            "FROM accounts WHERE id = :a"
        ),
        {"a": account_id},
    ).first()


def _org_install(conn, organization_id: UUID, connector_id: str) -> UUID | None:
    return conn.execute(
        sa.text(
            "SELECT id FROM auth_configs WHERE organization_id = :org "
            "AND connector_id = :c AND status = 'ACTIVE' "
            "ORDER BY is_default DESC, created_at LIMIT 1"
        ),
        {"org": organization_id, "c": connector_id},
    ).scalar()


def _unbacked_slug(
    conn, unbacked: dict, *, pod_id: UUID, account_id: UUID, auth_config_id, base
) -> str | None:
    """A SHARED connector with no account, one per (pod, account) it stood for."""
    if (pod_id, account_id) not in unbacked:
        if auth_config_id is None:
            return None
        slug = _free_slug(conn, pod_id, base)
        _insert_pod_connector(
            conn,
            pod_id=pod_id,
            slug=slug,
            auth_config_id=auth_config_id,
            mode="SHARED",
            shared_pod_account_id=None,
            created_by=None,
        )
        unbacked[(pod_id, account_id)] = slug
    return unbacked[(pod_id, account_id)]


def _stopped_slug(conn, unbacked: dict, row, account, suffix: str) -> str | None:
    """Where a binding the upgrade cannot keep running points instead."""
    if account.organization_id == row.organization_id:
        install = account.auth_config_id
    else:
        # Never a connector on another org's installation: the pod's own org's
        # installation of the same app, if it has one.
        install = _org_install(conn, row.organization_id, account.connector_id)
    return _unbacked_slug(
        conn,
        unbacked,
        pod_id=row.pod_id,
        account_id=account.id,
        auth_config_id=install,
        base=f"{_slug_base(account.connector_id)}{suffix}",
    )


def _shared_slug(conn, pod_id: UUID, account_id: UUID) -> str | None:
    """A SHARED connector running as this account, which its owner shared."""
    return conn.execute(
        sa.text(
            "SELECT pc.slug FROM pod_connectors pc JOIN pod_accounts pa "
            "ON pa.id = pc.shared_pod_account_id WHERE pc.pod_id = :pod "
            "AND pc.mode = 'SHARED' AND pa.account_id = :a "
            "AND pa.visibility <> 'PERSONAL' ORDER BY pc.created_at, pc.slug LIMIT 1"
        ),
        {"pod": pod_id, "a": account_id},
    ).scalar()


def _pod_account(conn, pod_id: UUID, account_id: UUID):
    return conn.execute(
        sa.text(
            "SELECT id, visibility, is_member_default FROM pod_accounts "
            "WHERE pod_id = :pod AND account_id = :a"
        ),
        {"pod": pod_id, "a": account_id},
    ).first()


def _surface_slug(conn, unbacked: dict, row) -> str | None:
    account = _account(conn, row.account_id)
    if account.organization_id != row.organization_id or not _is_pod_member(
        conn, row.pod_id, account.user_id
    ):
        return _stopped_slug(conn, unbacked, row, account, "-surface")
    existing = _pod_account(conn, row.pod_id, account.id)
    if existing is None:
        pod_account_id = _add_pod_account(
            conn, pod_id=row.pod_id, account=account, visibility="POD"
        )
    else:
        pod_account_id = existing.id
        if existing.visibility == "PERSONAL":
            conn.execute(
                sa.text("UPDATE pod_accounts SET visibility = 'POD' WHERE id = :id"),
                {"id": pod_account_id},
            )
    reused = conn.execute(
        sa.text(
            "SELECT slug FROM pod_connectors WHERE pod_id = :pod AND mode = "
            "'SHARED' AND shared_pod_account_id = :pa ORDER BY created_at, slug "
            "LIMIT 1"
        ),
        {"pod": row.pod_id, "pa": pod_account_id},
    ).scalar()
    if reused is not None:
        return reused
    slug = _free_slug(conn, row.pod_id, f"{_slug_base(account.connector_id)}-surface")
    _insert_pod_connector(
        conn,
        pod_id=row.pod_id,
        slug=slug,
        auth_config_id=account.auth_config_id,
        mode="SHARED",
        shared_pod_account_id=pod_account_id,
        created_by=account.user_id,
    )
    return slug


def _own_account_slug(conn, row, account) -> str:
    """The schedule owner's own account, through the per-member connector."""
    existing = _pod_account(conn, row.pod_id, account.id)
    if existing is None:
        _add_pod_account(
            conn, pod_id=row.pod_id, account=account, visibility="PERSONAL"
        )
    elif (
        not existing.is_member_default
        and not conn.execute(
            sa.text(
                "SELECT 1 FROM pod_accounts WHERE pod_id = :pod AND owner_user_id = :u "
                "AND auth_config_id = :ac AND is_member_default"
            ),
            {"pod": row.pod_id, "u": account.user_id, "ac": account.auth_config_id},
        ).scalar()
    ):
        conn.execute(
            sa.text("UPDATE pod_accounts SET is_member_default = true WHERE id = :id"),
            {"id": existing.id},
        )
    slug = conn.execute(
        sa.text(
            "SELECT slug FROM pod_connectors WHERE pod_id = :pod AND mode = "
            "'PER_MEMBER' AND auth_config_id = :ac ORDER BY created_at, slug LIMIT 1"
        ),
        {"pod": row.pod_id, "ac": account.auth_config_id},
    ).scalar()
    if slug is None:
        slug = _free_slug(conn, row.pod_id, _slug_base(account.connector_id))
        _insert_pod_connector(
            conn,
            pod_id=row.pod_id,
            slug=slug,
            auth_config_id=account.auth_config_id,
            mode="PER_MEMBER",
            shared_pod_account_id=None,
            created_by=account.user_id,
        )
    return slug


def _schedule_slug(conn, unbacked: dict, row) -> str | None:
    account = _account(conn, row.account_id)
    in_org = account.organization_id == row.organization_id
    if (
        in_org
        and account.user_id == row.user_id
        and _is_pod_member(conn, row.pod_id, account.user_id)
    ):
        return _own_account_slug(conn, row, account)
    shared = _shared_slug(conn, row.pod_id, account.id) if in_org else None
    return shared or _stopped_slug(conn, unbacked, row, account, "-shared")


def _bindings(conn, unbacked: dict) -> None:
    """Surfaces and schedules name a pod connector instead of an account."""
    for table, slug_for in (
        ("agent_surfaces", _surface_slug),
        ("schedules", _schedule_slug),
    ):
        owner = "s.user_id" if table == "schedules" else "NULL AS user_id"
        rows = conn.execute(
            sa.text(
                f"SELECT s.id, s.pod_id, s.account_id, {owner}, p.organization_id "  # noqa: S608
                f"FROM {table} s JOIN pods p ON p.id = s.pod_id "
                "WHERE s.account_id IS NOT NULL ORDER BY s.created_at, s.id"
            )
        ).all()
        unbound = 0
        for row in rows:
            slug = slug_for(conn, unbacked, row)
            if slug is None:
                unbound += 1
                _log().warning(
                    "0063: %s %s (pod %s) named account %s, which no pod connector "
                    "can serve; it is left with no connector and stops until one "
                    "is chosen.",
                    table,
                    row.id,
                    row.pod_id,
                    row.account_id,
                )
            conn.execute(
                sa.text(
                    f"UPDATE {table} SET pod_connector_slug = :slug WHERE id = :id"
                ),  # noqa: S608
                {"slug": slug, "id": row.id},
            )
        _log().info(
            "0063: %s: %d bound to a pod connector, %d left unbound.",
            table,
            len(rows) - unbound,
            unbound,
        )


def _unbind(conn) -> None:
    """Each binding back on the account its connector still resolves to."""
    shared = (
        "FROM pod_connectors pc JOIN pod_accounts pa "
        "ON pa.id = pc.shared_pod_account_id WHERE pc.pod_id = s.pod_id "
        "AND pc.slug = s.pod_connector_slug AND pc.mode = 'SHARED'"
    )
    for table in ("agent_surfaces", "schedules"):
        conn.execute(
            sa.text(f"UPDATE {table} s SET account_id = pa.account_id {shared}")  # noqa: S608
        )
    conn.execute(
        sa.text(
            "UPDATE schedules s SET account_id = pa.account_id FROM pod_connectors "
            "pc JOIN pod_accounts pa ON pa.pod_id = pc.pod_id AND "
            "pa.auth_config_id = pc.auth_config_id AND pa.is_member_default "
            "WHERE pc.pod_id = s.pod_id AND pc.slug = s.pod_connector_slug "
            "AND pc.mode = 'PER_MEMBER' AND pa.owner_user_id = s.user_id"
        )
    )


# ---------------------------------------------------------------------------
# Workstream C: how a pod connector was declared, and the account a standing
# binding pinned
# ---------------------------------------------------------------------------
#
# 1. `pod_connectors.source` (AUTO | MANUAL | IMPORT | MIGRATED, default
#    MANUAL). A connector the agent tools, the deprecated execute or a
#    deprecated `account_id` declare on a person's first use of their own
#    account is AUTO (`ensure_pod_connector_for_installation`). Every row that
#    exists when this runs was made by this migration's backfill: MIGRATED.
#
# 2. `schedules.pod_account_id`, FK `pod_accounts` ON DELETE SET NULL. On a
#    PER_MEMBER connector, the owner's pod account the schedule listens
#    through. Without it a schedule on the owner's second account (Telegram
#    bot B) resolved to their member default (bot A): B's schedule went quiet
#    and A's events fired twice. Backfilled from the old `account_id`, through
#    the pod account `_own_account_slug` put in the pod for it. SHARED
#    bindings pin nothing (they follow their connector), and surfaces only
#    ever bind SHARED connectors, so `agent_surfaces` gets no column.
#
# Model: `connectors/infrastructure/models/pod_connector.py` (PodConnector.source)
# and `schedule/infrastructure/models/schedule.py` (Schedule.pod_account_id,
# Index ix_schedules_pod_account).
#
# Order in `upgrade()`: after `_bindings(conn, unbacked)` and BEFORE
#   `ALTER TABLE schedules DROP COLUMN account_id` (it reads `account_id`).
# Order in `downgrade()`: after `_unbind(conn)` (which wrote the owner's
#   default back) and before `DROP TABLE pod_connectors` / `pod_accounts`.


def _upgrade_pinned_pod_accounts(conn) -> None:
    conn.execute(sa.text("UPDATE pod_connectors SET source = 'MIGRATED'"))
    conn.execute(sa.text("ALTER TABLE schedules ADD COLUMN pod_account_id UUID"))
    conn.execute(
        sa.text(
            "ALTER TABLE schedules ADD CONSTRAINT schedules_pod_account_id_fkey "
            "FOREIGN KEY (pod_account_id) REFERENCES pod_accounts (id) "
            "ON DELETE SET NULL"
        )
    )
    conn.execute(
        sa.text("CREATE INDEX ix_schedules_pod_account ON schedules (pod_account_id)")
    )
    # The account it named, where it is the owner's own on the PER_MEMBER
    # connector it now listens through.
    pinned = conn.execute(
        sa.text(
            "UPDATE schedules s SET pod_account_id = pa.id "
            "FROM pod_connectors pc, pod_accounts pa "
            "WHERE pc.pod_id = s.pod_id AND pc.slug = s.pod_connector_slug "
            "AND pc.mode = 'PER_MEMBER' AND pa.pod_id = s.pod_id "
            "AND pa.account_id = s.account_id AND pa.owner_user_id = s.user_id "
            "AND pa.auth_config_id = pc.auth_config_id"
        )
    ).rowcount
    _log().info("0063: schedules: %d pinned to their own pod account.", pinned)


def _downgrade_pinned_pod_accounts(conn) -> None:
    # The pinned account, not the owner's default `_unbind` wrote.
    conn.execute(
        sa.text(
            "UPDATE schedules s SET account_id = pa.account_id FROM pod_accounts pa "
            "WHERE pa.id = s.pod_account_id AND pa.owner_user_id = s.user_id"
        )
    )
    # Its FK and index go with it; `pod_connectors.source` goes with its table.
    conn.execute(sa.text("ALTER TABLE schedules DROP COLUMN pod_account_id"))


def _add_permissions(conn, permissions) -> None:
    for permission_id, scope, resource_type, description, roles in permissions:
        conn.execute(
            sa.text(
                "INSERT INTO auth_permissions (id, scope, resource_type, "
                "description, system_only, created_at, updated_at) VALUES "
                "(:id, :scope, :rt, :d, false, now(), now()) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": permission_id,
                "scope": scope,
                "rt": resource_type,
                "d": description,
            },
        )
        if roles:
            conn.execute(
                sa.text(
                    "INSERT INTO role_permissions (id, role_id, permission_id, "
                    "created_at) SELECT gen_random_uuid(), r.id, :p, now() "
                    "FROM roles r WHERE r.is_system AND r.name = ANY(:roles) "
                    "ON CONFLICT ON CONSTRAINT uq_role_permissions_role_permission "
                    "DO NOTHING"
                ),
                {"p": permission_id, "roles": list(roles)},
            )


def _drop_permissions(conn, permissions) -> None:
    # Cascades their role rows and every grant carrying them.
    conn.execute(
        sa.text("DELETE FROM auth_permissions WHERE id = ANY(:ids)"),
        {"ids": [permission[0] for permission in permissions]},
    )


# ---------------------------------------------------------------------------
# Installations of Lemma-operated apps, bound to one organization each (H4)
# ---------------------------------------------------------------------------
#
# A GitHub installation was only ever written down on accounts
# (`accounts.external_ref`), so any Lemma account whose GitHub user could see
# one could carry it, mint the App's tokens for it, and route its webhooks.
# `connector_installations` names its owner: one row per (connector, upstream
# id), unique. Generic, not GitHub's: any app Lemma operates across many
# upstream tenants has the same fact to record (Lemma's own Slack app does,
# in `slack_manager_installs`). DDL is the model's exactly
# (`connectors/infrastructure/models/connector_installation.py`).
#
# Backfill, from the accounts carrying each installation:
# - carried by accounts of exactly one organization: bound to it, attributed
#   to whoever connected it first;
# - carried by accounts of several: the owner cannot be derived, and guessing
#   is the vulnerability. Each is logged and those accounts' `external_ref`
#   is cleared; they bind again through the setup callback, where the first
#   organization to do so holds it.
# Every binding derived here is *unproven* (`proven_at` NULL): it keeps
# working for its organization, but nobody showed GitHub's proof for it, so
# it may be an installation somebody squatted on before H4. A proven claim
# from another organization (a GitHub admin of it, with
# `org.installation.manage`) takes it over; the holder's own next proven
# claim stamps `proven_at`, after which it is final.
# The downgrade drops the table. Cleared `external_ref`s stay cleared: putting
# a contested installation back on several organizations is what this ends.

_CONNECTOR_INSTALLATIONS = """
CREATE TABLE connector_installations (
	connector_id VARCHAR(255) NOT NULL,
	external_id VARCHAR(255) NOT NULL,
	organization_id UUID NOT NULL,
	bound_by_user_id UUID,
	proven_at TIMESTAMP WITH TIME ZONE,
	metadata JSONB DEFAULT '{}' NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_connector_installations_connector_external UNIQUE (connector_id, external_id),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
	FOREIGN KEY(bound_by_user_id) REFERENCES users (id) ON DELETE SET NULL
)
"""


def _upgrade_github_installation_org(conn) -> None:
    import logging

    log = logging.getLogger("alembic.runtime.migration")
    conn.execute(sa.text(_CONNECTOR_INSTALLATIONS))
    conn.execute(
        sa.text(
            "CREATE INDEX ix_connector_installations_organization_id "
            "ON connector_installations (organization_id)"
        )
    )
    first: dict[str, tuple[object, object]] = {}
    owners: dict[str, set[object]] = {}
    for installation_id, organization_id, user_id in conn.execute(
        sa.text(
            "SELECT external_ref, organization_id, user_id FROM accounts "
            "WHERE connector_id = 'github' AND external_ref IS NOT NULL "
            "ORDER BY external_ref, created_at, id"
        )
    ):
        owners.setdefault(installation_id, set()).add(organization_id)
        first.setdefault(installation_id, (organization_id, user_id))
    contested = sorted(i for i, orgs in owners.items() if len(orgs) > 1)
    for installation_id, (organization_id, user_id) in first.items():
        if installation_id in contested:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO connector_installations (id, created_at, "
                "connector_id, external_id, organization_id, bound_by_user_id) "
                "VALUES (gen_random_uuid(), now(), 'github', :installation_id, "
                ":organization_id, :user_id)"
            ),
            {
                "installation_id": installation_id,
                "organization_id": organization_id,
                "user_id": user_id,
            },
        )
    for installation_id in contested:
        log.warning(
            "0063: GitHub installation %s is carried by accounts in %d "
            "organizations (%s); its owner cannot be derived, so it is left "
            "unbound and those accounts must connect again.",
            installation_id,
            len(owners[installation_id]),
            ", ".join(sorted(str(one) for one in owners[installation_id])),
        )
    if contested:
        conn.execute(
            sa.text(
                "UPDATE accounts SET external_ref = NULL "
                "WHERE connector_id = 'github' AND external_ref = ANY(:contested)"
            ),
            {"contested": contested},
        )
    log.info(
        "0063: bound %d GitHub installations; %d contested and left unbound.",
        len(owners) - len(contested),
        len(contested),
    )


def _downgrade_github_installation_org(conn) -> None:
    conn.execute(sa.text("DROP TABLE connector_installations"))


# ---------------------------------------------------------------------------
# Deleting records is its own permission (audit H14)
# ---------------------------------------------------------------------------
# Deleting records is its own permission (audit H14)
# ---------------------------------------------------------------------------
#
# Deleting a table's rows asked for `datastore.record.write`, so whoever could
# write one row could empty the table, and a workload's delete was not
# destructive. `datastore.record.delete` is added, held by the system roles
# that hold the write (POD_USER, POD_EDITOR, POD_ADMIN, ORG_OWNER), and carried
# to everything that could delete before, so nothing that worked stops: every
# custom role holding `record.write` gets it, and every grant of `record.write`
# -- to a member, a role, an agent or a function, on a table or pod-wide --
# gets a twin of `record.delete` beside it. Grants to another pod are left out:
# a pod principal never holds anything but reads.
# The downgrade drops the permission, which cascades its role rows and grants.

_RECORD_DELETE = "datastore.record.delete"
_RECORD_WRITE = "datastore.record.write"
_RECORD_DELETE_HOLDERS = ("POD_USER", "POD_EDITOR", "POD_ADMIN", "ORG_OWNER")


def _upgrade_record_delete_permission(conn) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO auth_permissions (id, scope, resource_type, description, "
            "system_only, created_at, updated_at) VALUES (:id, 'POD', "
            "'datastore_record', 'Delete datastore records', false, now(), now()) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": _RECORD_DELETE},
    )
    # The system roles that hold it, and every other role holding the write.
    conn.execute(
        sa.text(
            "INSERT INTO role_permissions (id, role_id, permission_id, created_at) "
            "SELECT gen_random_uuid(), r.id, :delete, now() FROM roles r "
            "WHERE (r.is_system AND r.name = ANY(:holders)) OR EXISTS ("
            "SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id "
            "AND rp.permission_id = :write) "
            "ON CONFLICT ON CONSTRAINT uq_role_permissions_role_permission "
            "DO NOTHING"
        ),
        {
            "delete": _RECORD_DELETE,
            "write": _RECORD_WRITE,
            "holders": list(_RECORD_DELETE_HOLDERS),
        },
    )
    conn.execute(
        sa.text(
            "INSERT INTO resource_permission_grants (id, pod_id, resource_type, "
            "resource_id, grantee_type, grantee_id, permission_id, "
            "created_by_user_id, created_at) SELECT gen_random_uuid(), pod_id, "
            "resource_type, resource_id, grantee_type, grantee_id, :delete, "
            "created_by_user_id, created_at FROM resource_permission_grants "
            "WHERE permission_id = :write AND grantee_type <> 'POD' "
            "ON CONFLICT ON CONSTRAINT uq_resource_permission_grants_scope "
            "DO NOTHING"
        ),
        {"delete": _RECORD_DELETE, "write": _RECORD_WRITE},
    )


def _downgrade_record_delete_permission(conn) -> None:
    # Cascades its role rows and every grant carrying it.
    conn.execute(
        sa.text("DELETE FROM auth_permissions WHERE id = :id"),
        {"id": _RECORD_DELETE},
    )


# ---------------------------------------------------------------------------
# Slack identities email matching or forged deliveries left behind (M17, H9)
# ---------------------------------------------------------------------------
#
# The identity cache (`agent_surface_external_users.resolved_user_id`) records
# who a sender resolved to, never how. Before this PR a Slack sender was
# resolved by profile email -- which a workspace admin can set (M17) -- and a
# delivery on a bring-your-own app, whose signing secret a person holds, wrote
# its claimed sender's resolution too (H9). Those rows kept naming people.
#
# After this PR the only thing that names a Slack sender is Slack's proof of
# them in the workspace (`slack_workspace_people`: the installer's grant or
# Sign in with Slack), which `record_proof` writes through to the cache, or a
# binding made through the installation (`surface_verified_identities`), which
# is read before the cache and never written to it. So a Slack row is sound
# exactly when a proof in its workspace says the same; every other Slack
# resolution is cleared. The sender is re-proved on their next message: the
# proof path (`_slack_proof`) re-resolves a proven person and writes the row
# back; anyone else is nobody until they prove themselves. The read side
# (`agent_surfaces/services/slack_cached_identity.py`) applies the same rule at
# read time, for a row an older process writes after this ran.
#
# Rows on other platforms, and Slack rows' profile fields, are untouched.
# The downgrade restores nothing: the cleared answers were unproven, and the
# code before this migration would honour them again.


def _upgrade_slack_identity_reproof(conn) -> None:
    cleared = conn.execute(
        sa.text(
            "UPDATE agent_surface_external_users e SET resolved_user_id = NULL "
            "WHERE e.platform = 'SLACK' AND e.resolved_user_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM slack_workspace_people p "
            "WHERE p.team_id = e.tenant_id AND p.slack_user_id = e.external_user_id "
            "AND p.user_id = e.resolved_user_id)"
        )
    ).rowcount
    _log().info(
        "0063: cleared %d cached Slack sender identit(ies) no Slack proof backs; "
        "each is re-proved on its next message.",
        cleared,
    )


def _downgrade_slack_identity_reproof(conn) -> None:
    """Nothing to put back: what was cleared was never proven."""


# ---------------------------------------------------------------------------
# Lemma-made Slack apps say so on their install (replaces the surface flag)
# ---------------------------------------------------------------------------
#
# Whether Lemma made a teammate's Slack app -- through the manifest flow, so
# Slack handed its signing secret to Lemma and no person chose it -- is a fact
# about the app, and the app is its ORG_CUSTOM install. It lives in
# `auth_configs.metadata.app_made_by_lemma`; a surface reads it through its pod
# connector (`SurfaceAuthConfig.made_by_lemma`). On the surface it outlived the
# app: deleting the pod connector and re-declaring the slug on a bring-your-own
# install kept the surface trusting deliveries any secret holder can sign.
# (Not `connector_installations`: that table is who owns an upstream tenant --
# a GitHub installation, a Slack team -- unique across organizations; this is
# a property of one org's app registration, several per org.)
#
# Marked, on ORG_CUSTOM Slack installs:
# 1. the install behind any surface already saying `app_made_by_lemma: true`
#    (none on a database this branch never ran on; kept for one that did);
# 2. the install of a pending teammate app Lemma created
#    (`slack_pending_app_bindings.app_id` is set only by Lemma's own create);
# 3. an install named "<app name> (<app id>)", which is how
#    `SlackAppProvisioner._create` names Lemma's; one a person brings is
#    "<agent name> (<client id>)" (`slack_manager_bots._own_app_install`). A
#    Slack app id is `A` and upper-case letters and digits; a client id is two
#    numbers joined by a dot.
# Then the key comes off every surface, where nothing reads it any more.
# The downgrade takes the key off the installs.

_SLACK_ORG_APP = "c.connector_id = 'slack' AND c.config_source = 'ORG_CUSTOM'"

_MARK = (
    "UPDATE auth_configs c SET metadata = "
    "CASE WHEN jsonb_typeof(c.metadata) = 'object' THEN c.metadata "
    "ELSE '{}'::jsonb END || '{\"app_made_by_lemma\": true}'::jsonb "
    "WHERE " + _SLACK_ORG_APP + " AND "
)

#: Installs behind a surface that says Lemma made its app. The surface type is
#: compared after any enum-repr prefix, as ``to_entity`` reads it.
_BEHIND_A_FLAGGED_SURFACE = (
    "EXISTS (SELECT 1 FROM agent_surfaces s JOIN pod_connectors pc "
    "ON pc.pod_id = s.pod_id AND pc.slug = s.pod_connector_slug "
    "WHERE pc.auth_config_id = c.id "
    "AND regexp_replace(upper(s.surface_type), '^.*\\.', '') = 'SLACK' "
    "AND jsonb_typeof(s.config -> 'slack') = 'object' "
    "AND (s.config -> 'slack' ->> 'app_made_by_lemma') = 'true')"
)
_PENDING_LEMMA_APP = (
    "EXISTS (SELECT 1 FROM slack_pending_app_bindings b "
    "WHERE b.auth_config_id = c.id AND b.app_id IS NOT NULL)"
)
_NAMED_AFTER_AN_APP_ID = "c.name ~ ' \\(A[0-9A-Z]+\\)$'"


def _upgrade_slack_app_made_by_lemma(conn) -> None:
    marked = conn.execute(
        sa.text(
            _MARK
            + "("
            + " OR ".join(
                (_BEHIND_A_FLAGGED_SURFACE, _PENDING_LEMMA_APP, _NAMED_AFTER_AN_APP_ID)
            )
            + ")"
        )
    ).rowcount
    conn.execute(
        sa.text(
            "UPDATE agent_surfaces SET config = config #- '{slack,app_made_by_lemma}' "
            "WHERE jsonb_typeof(config -> 'slack') = 'object' "
            "AND (config -> 'slack') ? 'app_made_by_lemma'"
        )
    )
    _log().info("0063: marked %d Slack install(s) as apps Lemma made.", marked)


def _downgrade_slack_app_made_by_lemma(conn) -> None:
    conn.execute(
        sa.text(
            "UPDATE auth_configs SET metadata = metadata - 'app_made_by_lemma' "
            "WHERE jsonb_typeof(metadata) = 'object' "
            "AND metadata ? 'app_made_by_lemma'"
        )
    )


# ---------------------------------------------------------------------------
# Making a public link to a file is its own permission (audit M22)
# ---------------------------------------------------------------------------
#
# A public link hands a file to anyone holding it, outside every grant. Any
# `folder.read` holder could mint one -- a named agent included, with nothing
# granted for it. `folder.share` is that act, alone: every person role holds it
# (from `POD_VIEWER` up), and a named agent or function holds it only by a
# grant.
#
# Carried to everyone who could share before, so nothing a person did stops:
# every role -- system or custom -- that reads files gets it, and every grant
# that let a member or a role read files (`folder.read`, or `folder.write` /
# `folder.delete`, which imply it), on a file, a folder or pod-wide, gets a
# `folder.share` twin. Grants to an agent, a function, an app or another pod
# are left out: that is the one thing this changes.
#
# Links already minted are kept, the ones a workload minted included.
# `datastore_signed_links.minted_by_workload` still records which workload made
# each, and every fetch re-checks the link under that workload's authority, so
# a link stops serving as soon as its workload loses the read.
#
# The downgrade drops the permission, which cascades its role rows and grants.

_FOLDER_SHARE = "folder.share"
_FOLDER_READERS = ("folder.read", "folder.write", "folder.delete")


def _upgrade_folder_share_permission(conn) -> None:
    import logging

    conn.execute(
        sa.text(
            "INSERT INTO auth_permissions (id, scope, resource_type, description, "
            "system_only, created_at, updated_at) VALUES (:id, 'POD', 'folder', "
            "'Make public links to files', false, now(), now()) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": _FOLDER_SHARE},
    )
    roles = conn.execute(
        sa.text(
            "INSERT INTO role_permissions (id, role_id, permission_id, created_at) "
            "SELECT gen_random_uuid(), r.id, :share, now() FROM roles r "
            "WHERE EXISTS (SELECT 1 FROM role_permissions rp WHERE "
            "rp.role_id = r.id AND rp.permission_id = ANY(:readers)) "
            "ON CONFLICT ON CONSTRAINT uq_role_permissions_role_permission "
            "DO NOTHING"
        ),
        {"share": _FOLDER_SHARE, "readers": list(_FOLDER_READERS)},
    ).rowcount
    grants = conn.execute(
        sa.text(
            "INSERT INTO resource_permission_grants (id, pod_id, resource_type, "
            "resource_id, grantee_type, grantee_id, permission_id, "
            "created_by_user_id, created_at) SELECT DISTINCT ON (pod_id, "
            "resource_type, resource_id, grantee_type, grantee_id) "
            "gen_random_uuid(), pod_id, resource_type, resource_id, grantee_type, "
            "grantee_id, :share, created_by_user_id, created_at "
            "FROM resource_permission_grants "
            "WHERE permission_id = ANY(:readers) AND grantee_type = ANY(:people) "
            "ORDER BY pod_id, resource_type, resource_id, grantee_type, "
            "grantee_id, created_at "
            "ON CONFLICT ON CONSTRAINT uq_resource_permission_grants_scope "
            "DO NOTHING"
        ),
        {
            "share": _FOLDER_SHARE,
            "readers": list(_FOLDER_READERS),
            "people": list(_PEOPLE),
        },
    ).rowcount
    logging.getLogger("alembic.runtime.migration").info(
        "0063: gave folder.share to %d roles and %d member or role grants.",
        roles,
        grants,
    )


def _downgrade_folder_share_permission(conn) -> None:
    # Cascades its role rows and every grant carrying it.
    conn.execute(
        sa.text("DELETE FROM auth_permissions WHERE id = :id"),
        {"id": _FOLDER_SHARE},
    )


def upgrade() -> None:
    for statement in _NEW_TABLES.split(";"):
        if statement.strip():
            op.execute(statement)
    conn = op.get_bind()
    _add_permissions(conn, _ADDED)
    _per_member_connectors(conn)
    unbacked: dict[tuple[UUID, UUID], str] = {}
    _pinned_accounts(conn, unbacked)
    for statement in _SLUG_COLUMNS:
        op.execute(statement)
    _bindings(conn, unbacked)
    _upgrade_pinned_pod_accounts(conn)
    op.execute("ALTER TABLE agent_surfaces DROP COLUMN account_id")
    op.execute("ALTER TABLE schedules DROP COLUMN account_id")
    _drop_permissions(conn, _REMOVED)
    _upgrade_github_installation_org(conn)
    _upgrade_record_delete_permission(conn)
    _upgrade_slack_app_made_by_lemma(conn)
    _upgrade_slack_identity_reproof(conn)
    _upgrade_folder_share_permission(conn)


def _copy_grants(conn, *, from_type: str, to_type: str, rows) -> None:
    """Re-key grants, tolerating two new rows collapsing onto one old one."""
    for new_id, old_id in rows:
        conn.execute(
            sa.text(
                "INSERT INTO resource_permission_grants (id, pod_id, resource_type, "
                "resource_id, grantee_type, grantee_id, permission_id, "
                "created_by_user_id, created_at) SELECT gen_random_uuid(), pod_id, "
                ":to_type, :old, grantee_type, grantee_id, permission_id, "
                "created_by_user_id, created_at FROM resource_permission_grants "
                "WHERE resource_type = :from_type AND resource_id = :new "
                "ON CONFLICT ON CONSTRAINT uq_resource_permission_grants_scope "
                "DO NOTHING"
            ),
            {"to_type": to_type, "from_type": from_type, "old": old_id, "new": new_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM resource_permission_grants WHERE resource_type = "
                ":from_type AND resource_id = :new"
            ),
            {"from_type": from_type, "new": new_id},
        )


def downgrade() -> None:
    conn = op.get_bind()
    _downgrade_folder_share_permission(conn)
    _downgrade_slack_identity_reproof(conn)
    _downgrade_slack_app_made_by_lemma(conn)
    _downgrade_record_delete_permission(conn)
    _downgrade_github_installation_org(conn)
    for statement in _ACCOUNT_COLUMNS:
        op.execute(statement)
    _unbind(conn)
    _downgrade_pinned_pod_accounts(conn)
    # Their indexes go with the columns.
    op.execute("ALTER TABLE agent_surfaces DROP COLUMN pod_connector_slug")
    op.execute("ALTER TABLE schedules DROP COLUMN pod_connector_slug")
    _add_permissions(conn, _REMOVED)
    connectors = conn.execute(
        sa.text(
            "SELECT pc.id, ac.connector_id, pa.account_id, pc.pod_id "
            "FROM pod_connectors pc JOIN auth_configs ac ON ac.id = pc.auth_config_id "
            "LEFT JOIN pod_accounts pa ON pa.id = pc.shared_pod_account_id"
        )
    ).all()
    for pod_connector_id, connector_id, shared_account_id, pod_id in connectors:
        if shared_account_id is not None:
            # A shared connector was a pin: its workloads held the account.
            conn.execute(
                sa.text(
                    "INSERT INTO resource_permission_grants (id, pod_id, "
                    "resource_type, resource_id, grantee_type, grantee_id, "
                    "permission_id, created_by_user_id, created_at) SELECT "
                    "gen_random_uuid(), pod_id, 'connector_account', :a, "
                    "grantee_type, grantee_id, 'connector_account.use', "
                    "created_by_user_id, created_at FROM resource_permission_grants "
                    "WHERE resource_type = 'connector' AND resource_id = :pc "
                    "AND grantee_type IN ('AGENT', 'FUNCTION') "
                    "ON CONFLICT ON CONSTRAINT uq_resource_permission_grants_scope "
                    "DO NOTHING"
                ),
                {"a": shared_account_id, "pc": pod_connector_id},
            )
        _copy_grants(
            conn,
            from_type="connector",
            to_type="connector",
            rows=[(pod_connector_id, _legacy_connector_resource_id(connector_id))],
        )
    _copy_grants(
        conn,
        from_type="connector_account",
        to_type="connector_account",
        rows=conn.execute(sa.text("SELECT id, account_id FROM pod_accounts")).all(),
    )
    _drop_permissions(conn, _ADDED)
    op.execute("DROP TABLE pod_connectors")
    op.execute("DROP TABLE pod_accounts")
