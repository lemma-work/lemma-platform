"""Session-scoped approval store for workload actions (Redis-backed).

When a person resolves a ``request_approval`` with APPROVE_FOR_SESSION, the
approved permission is recorded here keyed to the conversation, the person who
approved it, and the workload actor. The authorizer then honors it as an
ephemeral grant — most importantly for DESTRUCTIVE_ACTIONS, which no workload
may perform by default.

The person is part of the key because a conversation is not one person's. In a
group conversation the same agent runs for whoever spoke last, and keyed on
``conversation:actor:permission`` alone, A's "allow for this conversation" was
read by the run B triggered: B's agent deleted with A's say-so, and since the
ceiling is the *running* person's, B never had to approve anything (M5). Keyed
on the person too, an approval covers only the runs of the person who gave it.

Beside the approvals sits the record of what was *denied*: which permissions
the authorizer refused for one exact tool call. An approval unlocks those and
nothing else. The ``permission_ids`` a ``request_approval`` carries are a tool
argument -- whatever the model wrote, and a model that read a poisoned page
writes ``pod.delete`` there -- so they were never something to record (H6).

Redis-backed (shared across replicas, like the role-snapshot cache) with a
config TTL. Redis being unavailable degrades to "no approval" — the safe
direction: the agent re-prompts instead of acting unapproved.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.authorization.outside_text import OUTSIDE_TEXT_REQUIRES_APPROVAL
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident

if TYPE_CHECKING:
    from app.core.authorization.context import Context, ResourceRef
    from app.core.authorization.delegation import DelegationClaims

logger = get_logger(__name__)
_store_incident = DependencyIncident("session_approval_store", logger=logger)

_approval_cache: RedisJsonCache | None = None


def exact_command_permission_id(tool_name: str, args: dict | None) -> str:
    """A stable "permission id" identifying one exact (tool_name, args) call.

    Used as a ``has_session_approval``/``record_session_approval`` key so a
    ``request_approval`` call with no structured ``permission_ids`` (e.g.
    ``exec_command``/``execute_python`` — these have no authorization gate at
    all, so there's nothing to derive a category from) still gets SOME
    APPROVE_FOR_SESSION reuse: approving one exact call lets the agent repeat
    that literal call again in the same conversation without re-prompting.

    Deliberately exact-match only, never a prefix/substring match: shell
    metacharacters (``;``, ``&&``, ``|``, backticks, command substitution) let
    an attacker smuggle extra commands after a prefix that looks identical to
    one the user already approved, so anything looser than an exact match on
    the full argument set would be a real injection vector.
    """
    canonical = json.dumps(
        args or {}, sort_keys=True, separators=(",", ":"), default=str
    )
    digest = hashlib.sha256(f"{tool_name}:{canonical}".encode("utf-8")).hexdigest()[:32]
    return f"exact_command:{tool_name}:{digest}"


def _get_approval_cache() -> RedisJsonCache | None:
    global _approval_cache
    ttl = settings.session_approval_ttl_seconds
    if ttl <= 0:
        return None
    if _approval_cache is None or _approval_cache._ttl_seconds != ttl:
        _approval_cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="authz:session-approval",
            ttl_seconds=ttl,
        )
    return _approval_cache


def _suffix(
    session_id: str,
    person_user_id: UUID,
    workload_actor_id: str,
    permission_id: str,
) -> str:
    return f"{session_id}:{person_user_id}:{workload_actor_id}:{permission_id}"


async def record_session_approval(
    *,
    session_id: str,
    person_user_id: UUID,
    workload_actor_id: str,
    permission_id: str,
) -> None:
    """Persist an APPROVE_FOR_SESSION decision for one permission.

    ``person_user_id`` is the person who approved, and the only person whose
    runs it covers: the lookup reads it from the delegated context's person.
    """
    cache = _get_approval_cache()
    if cache is None:
        return
    try:
        await cache.set_json(
            _suffix(session_id, person_user_id, workload_actor_id, permission_id),
            {
                "resolved_by_user_id": str(person_user_id),
                "approved_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as exc:
        _store_incident.record_failure(error_type=type(exc).__name__)
    else:
        _store_incident.record_success()


async def has_session_approval(
    *,
    session_id: str | None,
    person_user_id: UUID | None,
    workload_actor_id: str | None,
    permission_id: str,
) -> bool:
    """True when this person approved this action type for this workload+session.

    Any half of the key missing is "no": a run with no person behind it has
    nobody who could have approved anything.
    """
    if not session_id or not workload_actor_id or person_user_id is None:
        return False
    cache = _get_approval_cache()
    if cache is None:
        return False
    try:
        payload = await cache.get_json(
            _suffix(session_id, person_user_id, workload_actor_id, permission_id)
        )
    except Exception as exc:
        _store_incident.record_failure(error_type=type(exc).__name__)
        return False
    _store_incident.record_success()
    return payload is not None


#: What the denial record's Redis round trip can fail with: the server or the
#: network (``RedisError``, ``OSError``), or a value that is not JSON. Named
#: rather than ``Exception`` so a bug in this module is not mistaken for an
#: outage and swallowed.
_STORE_ERRORS = (RedisError, OSError, ValueError)


def _without_nulls(value: object) -> object:
    if isinstance(value, dict):
        return {k: _without_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_without_nulls(v) for v in value]
    return value


def _denial_suffix(
    session_id: str, workload_actor_id: str, tool_name: str, args: dict | None
) -> str:
    # Unset fields dropped: a tool records its refusal with its request model
    # dumped whole, nulls and all, and a model restating the call leaves them
    # out. Both are the same call, and must find the same record.
    call = exact_command_permission_id(tool_name, _without_nulls(args or {}))  # type: ignore[arg-type]
    return f"denied:{session_id}:{workload_actor_id}:{call}"


async def record_denial(
    *,
    session_id: str | None,
    workload_actor_id: str | None,
    tool_name: str,
    args: dict | None,
    permission_ids: Iterable[str],
) -> None:
    """Remember that the authorizer refused this exact call for these permissions.

    Written by the server where a refusal becomes a ``needs_approval`` result,
    and read back when the person answers the request it led to -- so what an
    approval unlocks is what the server refused, not what the model asked for.

    Accumulates per call rather than overwriting. The authorizer names only the
    first permission a call is missing, so a delete that needs both
    ``datastore.table.read`` and ``datastore.record.delete`` is refused twice,
    once for each; the second approval must cover both or the approved call
    stops at the first again, forever (DEV-ACCESS-002).

    No person in this key, deliberately: a denial grants nothing. It only says
    which permissions one exact call needs; whose approval may lend them, and
    up to which ceiling, is decided when the approval is used.
    """
    if not session_id or not workload_actor_id:
        return
    await _add_to_set(
        _denial_suffix(session_id, workload_actor_id, tool_name, args), permission_ids
    )


async def denied_permissions(
    *,
    session_id: str | None,
    workload_actor_id: str | None,
    tool_name: str,
    args: dict | None,
) -> frozenset[str]:
    """Every permission the server refused this exact call for, in this session."""
    if not session_id or not workload_actor_id:
        return frozenset()
    return await _read_set(
        _denial_suffix(session_id, workload_actor_id, tool_name, args)
    )


#: The refusals a person can answer by approving: a grant the workload lacks,
#: a destructive action no workload may take unasked, or an action an agent
#: takes while it reads what people outside the pod wrote (``outside_text``).
#: Anything else -- the person's own ceiling, a scope the token was narrowed
#: to -- no approval lifts.
APPROVABLE_REFUSALS = frozenset(
    {
        "MISSING_WORKLOAD_RESOURCE_GRANT",
        "DESTRUCTIVE_ACTION_REQUIRES_APPROVAL",
        OUTSIDE_TEXT_REQUIRES_APPROVAL,
    }
)


def _session_refusal_suffix(
    session_id: str, person_user_id: UUID, workload_actor_id: str
) -> str:
    return f"refused:{session_id}:{person_user_id}:{workload_actor_id}"


async def record_delegated_refusal(
    claims: "DelegationClaims", *, code: str, details: object
) -> None:
    """Remember what a delegated token was refused over HTTP, for its session.

    The sandbox's ``lemma`` CLI calls the API with the workload's token, and a
    refusal there reaches the model as a 403 in a shell's output -- text the
    model then paraphrases into ``request_approval(permission_ids=...)``. Those
    words are never recorded (H6), so without this an approved ``exec_command``
    had nothing to lend and "allow for this conversation" on a CLI delete
    unlocked nothing. Written by the error handler, from the refusal itself,
    keyed by the token's own (session, person, workload) -- the key the
    authorizer reads approvals back by.

    Per session rather than per call: a shell command is not the HTTP request
    it makes, so there is no exact call to key it by. An approval of a sandbox
    command lends what was pending here when its card was shown (``record_card``)
    -- never one recorded later -- and clears it.
    """
    if code not in APPROVABLE_REFUSALS or not isinstance(details, dict):
        return
    permission_ids = details.get("permission_ids")
    if not isinstance(permission_ids, list):
        return
    await _add_to_set(
        _session_refusal_suffix(
            claims.session_id,
            claims.invoked_by_user_id,
            # The same derivation the delegated context's ``actor_id`` uses.
            f"{claims.actor_type.value.lower()}:{claims.actor_id}",
        ),
        permission_ids,
    )


async def session_refusals(
    *, session_id: str, person_user_id: UUID, workload_actor_id: str
) -> frozenset[str]:
    """What this session's sandbox was refused, and not yet approved."""
    return await _read_set(
        _session_refusal_suffix(session_id, person_user_id, workload_actor_id)
    )


async def clear_session_refusals(
    *,
    session_id: str,
    person_user_id: UUID,
    workload_actor_id: str,
    permission_ids: Iterable[str],
) -> None:
    """Forget refusals an approval has now answered.

    Left in place, the next one-time approval of an unrelated command would
    lend them again -- an approval of ``ls`` carrying a delete refused an hour
    earlier. A refusal that recurs is simply recorded again.
    """
    suffix = _session_refusal_suffix(session_id, person_user_id, workload_actor_id)
    remaining = await _read_set(suffix) - set(permission_ids)
    cache = _get_approval_cache()
    if cache is None:
        return
    try:
        await cache.set_json(suffix, sorted(remaining))
    except _STORE_ERRORS as exc:
        _store_incident.record_failure(error_type=type(exc).__name__)
    else:
        _store_incident.record_success()


#: How long what a card showed stays readable. A card waits for its person as
#: long as it takes, so this outlives a session approval's own hour; an answer
#: given after it lapses lends nothing, and the call is refused and asks again.
_CARD_TTL_SECONDS = 7 * 24 * 3600


def _card_suffix(session_id: str, approval_id: str) -> str:
    return f"card:{session_id}:{approval_id}"


async def record_card(
    *, session_id: str, approval_id: str, card: dict[str, object]
) -> None:
    """Remember what an approval card put in front of its person.

    Written by the server when the card is shown, from the refusals pending
    then, and the only thing an answer to that card may lend. Without it the
    answer lent whatever was refused by the time it arrived: a sandbox refusal
    recorded after the card -- a ``job.py`` the agent had since edited to
    delete a table -- rode along on an approval of something else (H6).
    """
    cache = _get_approval_cache()
    if cache is None:
        return
    try:
        await cache.set_json(
            _card_suffix(session_id, approval_id), card, ttl_seconds=_CARD_TTL_SECONDS
        )
    except _STORE_ERRORS as exc:
        _store_incident.record_failure(error_type=type(exc).__name__)
    else:
        _store_incident.record_success()


async def shown_card(*, session_id: str, approval_id: str) -> dict[str, object]:
    """What :func:`record_card` wrote for this approval, or ``{}`` -- which
    lends nothing, the safe reading of a card nobody recorded."""
    cache = _get_approval_cache()
    if cache is None:
        return {}
    try:
        card = await cache.get_json(_card_suffix(session_id, approval_id))
    except _STORE_ERRORS as exc:
        _store_incident.record_failure(error_type=type(exc).__name__)
        return {}
    _store_incident.record_success()
    return card if isinstance(card, dict) else {}


def _offer_suffix(
    session_id: str, workload_actor_id: str, tool_name: str, args: dict | None
) -> str:
    return "offer:" + _denial_suffix(session_id, workload_actor_id, tool_name, args)


async def record_grant_offer(
    *,
    session_id: str | None,
    workload_actor_id: str | None,
    tool_name: str,
    args: dict | None,
    offer: dict[str, object],
) -> None:
    """Remember that approving this exact call would add a standing grant.

    Beside the denial, and keyed like it, for the same reason: what approving
    does is the server's to say, never the model's (H6).
    """
    cache = _get_approval_cache()
    if not session_id or not workload_actor_id or cache is None:
        return
    try:
        await cache.set_json(
            _offer_suffix(session_id, workload_actor_id, tool_name, args), offer
        )
    except _STORE_ERRORS as exc:
        _store_incident.record_failure(error_type=type(exc).__name__)
    else:
        _store_incident.record_success()


async def grant_offer(
    *,
    session_id: str | None,
    workload_actor_id: str | None,
    tool_name: str,
    args: dict | None,
) -> dict[str, object] | None:
    """The standing grant approving this exact call would add, if any."""
    cache = _get_approval_cache()
    if not session_id or not workload_actor_id or cache is None:
        return None
    try:
        offer = await cache.get_json(
            _offer_suffix(session_id, workload_actor_id, tool_name, args)
        )
    except _STORE_ERRORS as exc:
        _store_incident.record_failure(error_type=type(exc).__name__)
        return None
    _store_incident.record_success()
    return offer if isinstance(offer, dict) else None


async def revoke_session_approval(
    *,
    session_id: str,
    person_user_id: UUID,
    workload_actor_id: str,
    permission_id: str,
) -> None:
    """Withdraw one approval: the end of a one-time loan to a sandbox command."""
    cache = _get_approval_cache()
    if cache is None:
        return
    try:
        await cache.delete(
            _suffix(session_id, person_user_id, workload_actor_id, permission_id)
        )
    except _STORE_ERRORS as exc:
        _store_incident.record_failure(error_type=type(exc).__name__)
    else:
        _store_incident.record_success()


async def _add_to_set(suffix: str, permission_ids: Iterable[str]) -> None:
    wanted = {p for p in permission_ids if isinstance(p, str) and p}
    cache = _get_approval_cache()
    if not wanted or cache is None:
        return
    try:
        existing = await cache.get_json(suffix)
        if isinstance(existing, list):
            wanted |= {p for p in existing if isinstance(p, str) and p}
        await cache.set_json(suffix, sorted(wanted))
    except _STORE_ERRORS as exc:
        _store_incident.record_failure(error_type=type(exc).__name__)
    else:
        _store_incident.record_success()


async def _read_set(suffix: str) -> frozenset[str]:
    cache = _get_approval_cache()
    if cache is None:
        return frozenset()
    try:
        recorded = await cache.get_json(suffix)
    except _STORE_ERRORS as exc:
        _store_incident.record_failure(error_type=type(exc).__name__)
        return frozenset()
    _store_incident.record_success()
    if not isinstance(recorded, list):
        return frozenset()
    return frozenset(p for p in recorded if isinstance(p, str) and p)


async def also_refused(
    ctx: "Context", permission_id: str, resource: "ResourceRef | None"
) -> list[str]:
    """The rest of what this action needs that ``ctx`` would also be refused.

    The authorizer stops at the first missing permission, so a record delete
    was refused for reading the table, approved, run, refused again for the
    write, and approved again: two cards for one action. A caller that knows
    the action's whole requirement set says so in ``ctx.action_requirements``,
    and the refusal then names every approvable permission it is missing, so
    the person is asked once.

    Authorization only -- nothing runs. Only refusals an approval could lift
    are added; one the person's own ceiling causes stays out, since asking
    about it could not help.
    """
    missing: list[str] = []
    for other in sorted(ctx.action_requirements - {permission_id}):
        decision = await ctx._authorize(other, resource)  # noqa: SLF001
        if not decision.allowed and decision.reason_code in APPROVABLE_REFUSALS:
            missing.append(other)
    return missing


def approve_for_this_context(ctx: "Context", permission_ids: Iterable[str]) -> None:
    """Answer "yes" to the session-approval question for ``permission_ids`` --
    on this one context only.

    How a one-time approval reaches the authorizer. The approved call runs with
    the same delegated context it would have had, and the authorizer asks every
    ungranted or destructive check whether the person approved it; this answers
    that question for the approved permissions without writing anything a later
    call could find. The context is built for the one approved call and
    discarded after it, so nothing outlives the call -- which is what an
    APPROVE (not APPROVE_FOR_SESSION) means.

    It stands exactly where a session approval would, so it lifts no more than
    one: the delegation scope, the pod clamp and the person's ceiling all still
    apply. The approver can lend only what they themselves hold.
    """
    for permission_id in permission_ids:
        if isinstance(permission_id, str) and permission_id:
            ctx._session_approval_cache[permission_id] = True  # noqa: SLF001
