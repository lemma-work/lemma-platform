"""Who may read a file, worked out once per request and applied as plain SQL.

``allowed_actions_expr`` answers the question row by row: for every row it
evaluates a CASE whose grant branches are correlated EXISTS over the grants
table, joined back to the file table to follow folder grants down the tree,
three times over (read, write, delete). That is the right shape for projecting
the actions of the handful of rows a page shows, and the wrong one for deciding
which of a pod's thousands of rows to show at all: the cost is rows x actions x
grants, and before the cascade was split it was rows x actions x grants x every
file on the platform.

The caller's authority over a pod is small and does not depend on the row. So
it is read first -- one query for the grants that can matter, one for the
folders that hide what is under them -- and what is left per row is a CASE over
the row's own columns with constants and short arrays in it. No subquery runs
per row.

``FileReadRule.readable`` must agree exactly with ``allowed_actions_contains(
allowed_actions_expr(...), folder.read)`` for every context and every row, and
``BlockedFolders`` exactly with ``has_unreadable_ancestor``. Both are checked
against those expressions on randomised pods in
``tests/e2e/test_file_read_rule_equivalence_e2e.py``. A change to the rule in
``sql_actions`` that is not made here too fails there.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import (
    Boolean,
    String,
    and_,
    any_,
    case,
    exists,
    false,
    func,
    literal,
    or_,
    select,
    true,
    union_all,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.orm.util import AliasedClass
from sqlalchemy.sql import ColumnElement

from app.core.authorization.context import (
    ActorType,
    Context,
    PrincipalRef,
    ResourceType,
)
from app.core.authorization.grants import grant_resource_type_values
from app.core.authorization.models import ResourcePermissionGrantModel
from app.core.authorization.permissions import (
    equivalent_permission_ids,
    pod_principal_may_hold,
)
from app.core.authorization.resource_actions import owner_actions_for_resource
from app.core.authorization.sql_actions import read_action_for_resource
from app.modules.datastore.domain.file_entities import FileKind, FileStatus
from app.modules.datastore.infrastructure.models import DatastoreFile

_READ = read_action_for_resource(ResourceType.DOCUMENT)

#: The visibility values the CASE names. Anything else falls to its ELSE.
_KNOWN_VISIBILITIES = ("POD", "PUBLIC", "RESTRICTED", "PERSONAL")

#: Which file table a predicate is written over: the table itself, or an alias
#: of it when a statement reads the table twice.
type FileTable = type[DatastoreFile] | AliasedClass[DatastoreFile]


def _text_array(values: Iterable[str]) -> ColumnElement:
    return literal(sorted(values), type_=ARRAY(String))


def _starts_with_any(path_col, prefixes: Iterable[str]) -> ColumnElement[bool]:
    """``path ^@ ANY(prefixes)``: a byte-wise prefix test against a short list.

    Not ``LIKE``: a path may hold ``%`` or ``_``. And not a range comparison,
    which is only correct under byte ordering while this database collates by
    locale. ``^@`` is the same test the cascade's ``left(path, n) = prefix``
    makes, without needing the prefix's length per element.
    """
    return path_col.op("^@", return_type=Boolean)(any_(_text_array(prefixes)))


@dataclass(frozen=True, slots=True)
class GrantReach:
    """What one principal group's read grants cover in one pod."""

    pod_wide: bool = False
    #: Live paths of the rows the grants point at, folders and documents alike.
    paths: frozenset[str] = frozenset()

    def covers(self, path_col) -> ColumnElement[bool]:
        """The cascade: the granted row itself, or anything beneath it."""
        if self.pod_wide:
            return true()
        if not self.paths:
            return false()
        return or_(
            path_col == any_(_text_array(self.paths)),
            _starts_with_any(path_col, (f"{path}/" for path in self.paths)),
        )


def grant_groups(ctx: Context) -> tuple[frozenset[PrincipalRef], ...]:
    """The principal groups whose read grants decide some row for *ctx*.

    A row is granted when *every* group holds a grant reaching it (the
    delegation intersection); a group with no principals reaches nothing.
    Empty when no grant can decide anything for this context.
    """
    if ctx.is_superuser:
        return ()
    if ctx.actor_type == ActorType.POD:
        if ctx.pod_id is None or not pod_principal_may_hold(_READ):
            return ()
        return ctx.grant_principal_sets or (ctx.principal_refs,)
    if ctx.is_outsider:
        return ()
    if (
        ctx.actor_type == ActorType.DELEGATED_USER_WORKLOAD
        and ctx.workload_principal_refs
    ):
        return (ctx.workload_principal_refs,)
    return ctx.grant_principal_sets or (ctx.principal_refs,)


def grant_reach_statement(pod_id: UUID, principals: frozenset[PrincipalRef]):
    """The pod's read grants held by any of *principals*, with live paths."""
    grant = ResourcePermissionGrantModel
    target = aliased(DatastoreFile)
    return (
        select(grant.grantee_type, grant.grantee_id, grant.resource_id, target.path)
        .outerjoin(
            target,
            and_(target.id == grant.resource_id, target.pod_id == grant.pod_id),
        )
        .where(
            grant.pod_id == pod_id,
            grant.resource_type.in_(grant_resource_type_values(ResourceType.DOCUMENT)),
            grant.permission_id.in_(equivalent_permission_ids(_READ)),
            or_(
                *(
                    and_(
                        grant.grantee_type == principal.type,
                        grant.grantee_id == principal.id,
                    )
                    for principal in principals
                )
            ),
        )
    )


async def resolve_grant_reach(
    session: AsyncSession,
    *,
    pod_id: UUID,
    groups: tuple[frozenset[PrincipalRef], ...],
) -> tuple[GrantReach, ...]:
    """Every group's read grants in this pod, in one indexed read.

    Folder grants are resolved to the folder's *current* path here, once, which
    is what the cascade did per row by joining back to the file table.
    """
    if not groups or any(not group for group in groups):
        return tuple(GrantReach() for _ in groups)
    rows = await session.execute(
        grant_reach_statement(pod_id, frozenset().union(*groups))
    )
    held: dict[tuple[str, UUID], list[tuple[UUID, str | None]]] = {}
    for grantee_type, grantee_id, resource_id, path in rows.all():
        held.setdefault((grantee_type, grantee_id), []).append((resource_id, path))
    reaches: list[GrantReach] = []
    for group in groups:
        grants = [
            grant
            for principal in group
            for grant in held.get((principal.type, principal.id), ())
        ]
        reaches.append(
            GrantReach(
                pod_wide=any(resource_id == pod_id for resource_id, _ in grants),
                paths=frozenset(path for _, path in grants if path is not None),
            )
        )
    return tuple(reaches)


@dataclass(frozen=True, slots=True)
class FileReadRule:
    """The read decision for one context, with its grants already resolved."""

    ctx: Context
    reaches: tuple[GrantReach, ...]

    @property
    def reads_everything(self) -> bool:
        return self.ctx.is_superuser

    def readable(self, file: FileTable = DatastoreFile) -> ColumnElement[bool]:
        """Whether the row alone may be read: no folder above it consulted.

        Mirrors ``allowed_actions_expr`` branch for branch, restricted to the
        read action. The CASE is kept rather than flattened into ORs because
        its first-match order is part of the rule (an owner who could not read
        by role still reads their own row; a PERSONAL row is denied before
        anything else is asked).
        """
        ctx = self.ctx
        if ctx.is_superuser:
            return true()
        if ctx.actor_type == ActorType.POD:
            return self._pod_readable(file)
        if ctx.is_outsider:
            is_public = file.visibility == "PUBLIC"
            if ctx.pod_id is not None:
                is_public = and_(is_public, file.pod_id == ctx.pod_id)
            return case((is_public, true()), else_=false())
        granted = self._granted(file)
        if (
            ctx.actor_type == ActorType.DELEGATED_USER_WORKLOAD
            and ctx.workload_principal_refs
        ):
            if ctx.user_id is not None:
                personal_denied = and_(
                    file.visibility == "PERSONAL",
                    or_(
                        file.owner_user_id.is_(None),
                        file.owner_user_id != ctx.user_id,
                    ),
                )
            else:
                personal_denied = file.visibility == "PERSONAL"
            return case((personal_denied, false()), else_=granted)

        role_read = ctx.has_permission(_READ)
        owner_read = role_read or _READ in owner_actions_for_resource(
            ResourceType.DOCUMENT
        )
        public_read = role_read or (
            ctx.actor_type == ActorType.USER and ctx.user_id is not None
        )
        whens: list[tuple[ColumnElement[bool], ColumnElement[bool]]] = []
        if ctx.user_id is not None:
            whens.append((file.owner_user_id == ctx.user_id, _bool(owner_read)))
        whens.extend(
            [
                (file.visibility == "POD", _bool(role_read)),
                (file.visibility == "PUBLIC", _bool(public_read)),
                (file.visibility == "RESTRICTED", granted),
                (file.visibility == "PERSONAL", false()),
            ]
        )
        return case(*whens, else_=false())

    def possibly_unreadable(self, file: FileTable = DatastoreFile):
        """A visibility filter wide enough to hold every row this rule denies.

        Only a hint for an index on visibility; ``readable`` still decides.
        """
        ctx = self.ctx
        values = {"PERSONAL", "RESTRICTED"}
        if not ctx.has_permission(_READ):
            values.add("POD")
            if not (ctx.actor_type == ActorType.USER and ctx.user_id is not None):
                values.add("PUBLIC")
        if ctx.actor_type == ActorType.POD or ctx.is_outsider:
            values.update(_KNOWN_VISIBILITIES)
        if (
            ctx.actor_type == ActorType.DELEGATED_USER_WORKLOAD
            and ctx.workload_principal_refs
        ):
            values.update(_KNOWN_VISIBILITIES)
        return or_(
            file.visibility.in_(sorted(values)),
            file.visibility.not_in(_KNOWN_VISIBILITIES),
        )

    def _pod_readable(self, file: FileTable) -> ColumnElement[bool]:
        ctx = self.ctx
        if ctx.pod_id is None or not pod_principal_may_hold(_READ):
            return false()
        return case(
            (file.pod_id != ctx.pod_id, false()),
            (file.visibility == "PERSONAL", false()),
            (file.visibility == "PUBLIC", true()),
            else_=self._granted(file),
        )

    def _granted(self, file: FileTable) -> ColumnElement[bool]:
        if not self.reaches:
            return false()
        return and_(*(reach.covers(file.path) for reach in self.reaches))


def _bool(value: bool) -> ColumnElement[bool]:
    return true() if value else false()


async def resolve_read_rule(
    session: AsyncSession, *, pod_id: UUID, ctx: Context
) -> FileReadRule:
    groups = grant_groups(ctx)
    reaches = (
        await resolve_grant_reach(session, pod_id=pod_id, groups=groups)
        if groups
        else ()
    )
    return FileReadRule(ctx=ctx, reaches=reaches)


@dataclass(frozen=True, slots=True)
class BlockedFolders:
    """Rows the caller cannot read that have rows beneath them.

    The ancestor walk hides a row when any row above it is unreadable. Above a
    row means a path that is a prefix of its path ending at a ``/`` -- with the
    root spelled ``/``, so ``/`` is above every other path that starts with a
    slash. Rather than finding each row's ancestors and testing them, find the
    few unreadable rows that are anyone's ancestor once, and test each row's
    path against their prefixes.
    """

    #: Whether the root row ``/`` itself is unreadable.
    root: bool = False
    #: ``path + "/"`` for each outermost blocked row other than the root.
    prefixes: frozenset[str] = frozenset()

    @classmethod
    def of(cls, paths: Iterable[str]) -> "BlockedFolders":
        """The outermost of *paths*: anything under another adds nothing."""
        blocked = set(paths)
        root = "/" in blocked
        outermost: set[str] = set()
        for path in blocked:
            if path in ("", "/"):
                # The empty path is nobody's ancestor (the walk spells the
                # root ``/``, never ``""``).
                continue
            if root and path.startswith("/"):
                continue
            if any(
                path[:index] in blocked
                for index in range(1, len(path))
                if path[index] == "/"
            ):
                continue
            outermost.add(path)
        return cls(root=root, prefixes=frozenset(f"{p}/" for p in outermost))

    @property
    def empty(self) -> bool:
        return not self.root and not self.prefixes

    def hides(self, path_col) -> ColumnElement[bool]:
        clauses: list[ColumnElement[bool]] = []
        if self.root:
            clauses.append(
                and_(path_col.op("^@", return_type=Boolean)("/"), path_col != "/")
            )
        if self.prefixes:
            clauses.append(_starts_with_any(path_col, self.prefixes))
        return or_(*clauses) if clauses else false()


async def resolve_blocked_folders(
    session: AsyncSession, *, pod_id: UUID, rule: FileReadRule
) -> BlockedFolders:
    """The unreadable rows that are some row's ancestor, in one statement.

    Folders are taken whole: an unreadable folder with nothing under it hides
    nothing, so there is no reason to ask. Other rows are rarely anyone's
    ancestor, but the walk never assumed so and neither does this -- each
    unreadable non-folder is kept only if some row sits beneath it, found by a
    byte-wise range probe on the ``(pod_id, path text_pattern_ops)`` index.
    """
    if rule.reads_everything:
        return BlockedFolders()
    result = await session.execute(blocked_folders_statement(pod_id, rule))
    return BlockedFolders.of(result.scalars().all())


def blocked_folders_statement(pod_id: UUID, rule: FileReadRule):
    """Paths of the unreadable rows in the pod that may have rows beneath them."""
    row = DatastoreFile
    below = aliased(DatastoreFile)
    unreadable = ~rule.readable(row)
    folders = select(row.path).where(
        row.pod_id == pod_id,
        row.kind == FileKind.FOLDER.value,
        rule.possibly_unreadable(row),
        unreadable,
    )
    with_descendants = select(row.path).where(
        row.pod_id == pod_id,
        row.kind != FileKind.FOLDER.value,
        unreadable,
        or_(
            row.path == "/",
            exists().where(
                below.pod_id == row.pod_id,
                # Function calls rather than ``||``: Postgres gives these
                # operators the same precedence as ``||``, and SQLAlchemy does
                # not know to parenthesize, so ``a ~>=~ b || '/'`` would parse
                # as ``(a ~>=~ b) || '/'``.
                below.path.op("~>=~", return_type=Boolean)(func.concat(row.path, "/")),
                below.path.op("~<~", return_type=Boolean)(func.concat(row.path, "0")),
            ),
        ),
    )
    return union_all(folders, with_descendants)


#: Statuses a file that was indexed before can be in while it still has chunks.
_REINDEXING = (
    FileStatus.PENDING.value,
    FileStatus.PROCESSING.value,
    FileStatus.FAILED_PERMANENT.value,
)


def _searchable(pod_id: UUID) -> list[ColumnElement[bool]]:
    """The files in the pod a chunk search can match.

    Indexed and enabled: nothing else has chunks to find, so neither side of
    the split spends its length on folders or on documents that were never
    indexed. A file being reprocessed -- or whose newest bytes failed -- keeps
    the revision it was last indexed at (``indexed_sha256``), and that stays
    findable until a new one replaces it, on whichever side the caller's
    access puts it. Served by ``ix_datastore_file_status``.
    """
    return [
        DatastoreFile.pod_id == pod_id,
        or_(
            DatastoreFile.status == FileStatus.COMPLETED.value,
            and_(
                DatastoreFile.status.in_(_REINDEXING),
                DatastoreFile.indexed_sha256.is_not(None),
            ),
        ),
        DatastoreFile.kind == FileKind.FILE.value,
        DatastoreFile.search_enabled,
    ]


def searchable_files_statement(pod_id: UUID, readable: ColumnElement[bool]):
    """``(id, readable)`` for every file in the pod a chunk search can match."""
    return select(DatastoreFile.id, readable).where(*_searchable(pod_id))


def searchable_side_statement(
    pod_id: UUID,
    readable: ColumnElement[bool],
    *,
    readable_side: bool,
    limit: int | None,
):
    """The ids on one side of the split, at most *limit* of them.

    The split only has to say which side is shorter and whether that side is
    under the pushdown ceiling, so neither side needs reading past
    ``ceiling + 1``: a side cut off there is "more than the ceiling", which is
    all the decision uses. A predicate that is NULL for a row counts it hidden,
    as reading ``(id, readable)`` did.
    """
    side = readable if readable_side else ~func.coalesce(readable, False)
    statement = select(DatastoreFile.id).where(*_searchable(pod_id), side)
    return statement if limit is None else statement.limit(limit)
