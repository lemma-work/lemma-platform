"""The resolved read rule's construction: shapes, pruning and the scope choice.

What the rule *answers* is pinned against the per-row CASE on real pods in
``tests/e2e/test_file_read_rule_equivalence_e2e.py``. These cover what can be
decided without a database: that no predicate carries a subquery, that blocked
folders are pruned to the outermost, and which id set search sends.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.core.authorization.context import (
    Actor,
    Context,
    OtherPod,
    Outsider,
    OutsiderKind,
    Person,
    PrincipalRef,
    Workload,
    WorkloadKind,
)
from app.core.authorization.permissions import Permissions
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.search_scope import (
    SearchablePartition,
    SearchFileScope,
    SearchScopeTimedOut,
)
from app.modules.datastore.infrastructure.models import DatastoreFile
from app.modules.datastore.infrastructure.repositories.file_read_rule import (
    BlockedFolders,
    FileReadRule,
    GrantReach,
    grant_groups,
)
from app.modules.datastore.services.files.authorizer import FileAuthorizer
from app.modules.datastore.services.files.path_resolver import PathResolver

_POD = uuid4()
_ROLE = PrincipalRef("ROLE", uuid4())
_AGENT = PrincipalRef("AGENT", uuid4())
_OTHER_POD = PrincipalRef("POD", uuid4())


def _sql(expression) -> str:
    return str(
        expression.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def _ctx(actor: Actor, **kwargs) -> Context:
    kwargs.setdefault("pod_id", _POD)
    return Context(actor=actor, actor_id="t", authorizer=None, **kwargs)


_MEMBER_ID = uuid4()
_INVOKER_ID = uuid4()
_CONTEXTS = {
    "member": _ctx(
        Person(user_id=_MEMBER_ID),
        user_id=_MEMBER_ID,
        permission_ids=frozenset({Permissions.FOLDER_READ}),
        principal_refs=frozenset({_ROLE}),
    ),
    "delegated": _ctx(
        Workload(
            kind=WorkloadKind.AGENT,
            workload_id=_AGENT.id,
            for_user_id=_INVOKER_ID,
            principal_refs=frozenset({_AGENT}),
        ),
        user_id=_INVOKER_ID,
        principal_refs=frozenset({_ROLE, _AGENT}),
    ),
    "pod": _ctx(OtherPod(pod_id=_OTHER_POD.id), principal_refs=frozenset({_OTHER_POD})),
    "anonymous": _ctx(Outsider(kind=OutsiderKind.ANONYMOUS)),
    "superuser": _ctx(Person(user_id=uuid4()), is_superuser=True),
}


@pytest.mark.parametrize("name", sorted(_CONTEXTS))
def test_the_readable_predicate_runs_no_subquery_per_row(name: str) -> None:
    rule = FileReadRule(
        ctx=_CONTEXTS[name],
        reaches=(GrantReach(pod_wide=False, paths=frozenset({"/docs", "/a%b"})),),
    )
    sql = _sql(rule.readable()).upper()
    assert "SELECT" not in sql and "EXISTS" not in sql, (
        f"{name}: the predicate correlates a subquery per row:\n{sql}"
    )


def test_a_grant_reaches_its_row_and_everything_beneath_it() -> None:
    sql = _sql(GrantReach(paths=frozenset({"/docs"})).covers(DatastoreFile.path))
    assert "= ANY (ARRAY['/docs'])" in sql
    # The prefix carries the separator, so "/docs" does not reach "/docs-old".
    assert "^@ ANY (ARRAY['/docs/'])" in sql


def test_a_pod_wide_grant_reaches_everything_and_no_grant_reaches_nothing() -> None:
    assert _sql(GrantReach(pod_wide=True).covers(DatastoreFile.path)) == "true"
    assert _sql(GrantReach().covers(DatastoreFile.path)) == "false"


def test_every_principal_group_must_reach_the_row() -> None:
    rule = FileReadRule(
        ctx=_CONTEXTS["delegated"],
        reaches=(GrantReach(paths=frozenset({"/a"})), GrantReach()),
    )
    assert "false" in _sql(rule.readable()).lower()


def test_which_groups_decide_grants() -> None:
    assert grant_groups(_CONTEXTS["superuser"]) == ()
    assert grant_groups(_CONTEXTS["anonymous"]) == ()
    assert grant_groups(_CONTEXTS["delegated"]) == (frozenset({_AGENT}),)
    assert grant_groups(_CONTEXTS["member"]) == (frozenset({_ROLE}),)
    assert grant_groups(_CONTEXTS["pod"]) == (frozenset({_OTHER_POD}),)
    unpinned = _ctx(
        OtherPod(pod_id=_OTHER_POD.id),
        pod_id=None,
        principal_refs=frozenset({_OTHER_POD}),
    )
    assert grant_groups(unpinned) == ()


def test_blocked_folders_keep_only_the_outermost() -> None:
    blocked = BlockedFolders.of(["/a", "/a/b", "/a/b/c", "/a-b", "/x/y", "/x/y/z"])
    assert blocked == BlockedFolders(
        root=False, prefixes=frozenset({"/a/", "/a-b/", "/x/y/"})
    )


def test_a_blocked_root_hides_every_other_rooted_path() -> None:
    blocked = BlockedFolders.of(["/", "/a", "rootless/b", ""])
    # Everything under "/" is already hidden; a path that does not start with
    # a slash is not under it, and the empty path is nobody's ancestor.
    assert blocked == BlockedFolders(root=True, prefixes=frozenset({"rootless/b/"}))
    sql = _sql(blocked.hides(DatastoreFile.path))
    assert "^@ '/'" in sql and "!= '/'" in sql, sql


def test_nothing_blocked_hides_nothing() -> None:
    assert BlockedFolders.of([]).empty
    assert _sql(BlockedFolders().hides(DatastoreFile.path)) == "false"


@pytest.mark.parametrize(
    ("scope", "clause"),
    [
        (SearchFileScope.only([uuid4()]), "AND rc.file_id = ANY(:ids)"),
        (SearchFileScope.all_but([uuid4()]), "AND rc.file_id <> ALL(:ids)"),
        (SearchFileScope.all_but([]), ""),
        (SearchFileScope.post_filtered(), ""),
    ],
)
def test_the_chunk_clause_for_each_scope(scope: SearchFileScope, clause: str) -> None:
    assert scope.sql_clause("rc.file_id", "ids") == clause
    assert scope.binds == bool(clause)


class _Partitions:
    """The repository's split, as data; or a timeout."""

    def __init__(self, readable: int, hidden: int, *, timed_out: bool = False):
        self.readable = [uuid4() for _ in range(readable)]
        self.hidden = [uuid4() for _ in range(hidden)]
        self.partition = SearchablePartition(
            readable=frozenset(self.readable), hidden=frozenset(self.hidden)
        )
        self.timed_out = timed_out
        self.asked: list[dict] = []

    async def searchable_partition(
        self, *, limit=None, readable_only=False, **_kwargs
    ) -> SearchablePartition:
        """Each side cut off at *limit*, the way the repository reads it."""
        self.asked.append({"limit": limit, "readable_only": readable_only})
        if self.timed_out:
            raise SearchScopeTimedOut("too slow")
        return SearchablePartition(
            readable=frozenset(self.readable[:limit]),
            hidden=frozenset() if readable_only else frozenset(self.hidden[:limit]),
        )


async def _scope(repository: _Partitions) -> SearchFileScope:
    authorizer = FileAuthorizer(object(), repository, PathResolver())
    return await authorizer.search_file_scope(pod_id=_POD, ctx=_CONTEXTS["member"])


async def test_search_sends_the_shorter_side(monkeypatch) -> None:
    monkeypatch.setattr(
        datastore_settings, "datastore_search_readable_id_pushdown_limit", 100
    )
    narrow = _Partitions(readable=3, hidden=50)
    scope = await _scope(narrow)
    assert scope.enumerated and scope.file_ids == narrow.partition.readable

    wide = _Partitions(readable=50, hidden=3)
    scope = await _scope(wide)
    assert scope.excluding and scope.file_ids == wide.partition.hidden
    assert not scope.enumerated, "an exclusion still owes its results a check"

    tie = _Partitions(readable=4, hidden=4)
    assert (await _scope(tie)).enumerated, "a tie goes to the exact filter"


async def test_both_sides_over_the_ceiling_are_not_sent(monkeypatch) -> None:
    monkeypatch.setattr(
        datastore_settings, "datastore_search_readable_id_pushdown_limit", 10
    )
    scope = await _scope(_Partitions(readable=20, hidden=30))
    assert not scope.narrowed and not scope.enumerated


async def test_the_split_is_read_no_further_than_one_past_the_ceiling(
    monkeypatch,
) -> None:
    """Which side is shorter, and whether it is under the ceiling, is all the
    choice needs -- so neither side is read past ``ceiling + 1``, however
    large the pod."""
    monkeypatch.setattr(
        datastore_settings, "datastore_search_readable_id_pushdown_limit", 10
    )
    huge = _Partitions(readable=5_000, hidden=3)
    scope = await _scope(huge)
    assert huge.asked == [{"limit": 11, "readable_only": False}]
    assert scope.excluding and scope.file_ids == huge.partition.hidden

    both_over = _Partitions(readable=5_000, hidden=4_000)
    assert await _scope(both_over) == SearchFileScope.post_filtered(), (
        "two sides cut off at the same limit are both over the ceiling"
    )


async def test_widening_reads_the_whole_readable_side_and_nothing_else() -> None:
    repository = _Partitions(readable=50, hidden=7)
    authorizer = FileAuthorizer(object(), repository, PathResolver())
    scope = await authorizer.readable_file_scope(pod_id=_POD, ctx=_CONTEXTS["member"])
    assert repository.asked == [{"limit": None, "readable_only": True}]
    assert scope.enumerated and scope.file_ids == repository.partition.readable


async def test_a_timed_out_scope_is_post_filtered() -> None:
    scope = await _scope(_Partitions(readable=1, hidden=1, timed_out=True))
    assert scope == SearchFileScope.post_filtered()
