"""What the server refused an agent's call for: the only thing an approval unlocks.

A refusal reaches the model as a ``needs_approval`` result whose ``approval``
envelope names the call to re-issue and the permissions it was refused for. The
model then calls ``request_approval`` and copies those ``permission_ids`` across
-- or does not: they are a tool argument, and a model that has read a poisoned
page writes ``["pod.delete"]`` there. Approving that "for the session" recorded
whatever it said, so one click on a harmless call minted the grant the page
asked for (H6).

So the refusal is written down here, by the server, at the moment it becomes a
``needs_approval`` result; and when the card is shown, what is pending then is
written on the card (``show_card``). The person's answer lends exactly that
(``card_lends``): not the model's copy, and not a refusal recorded after the
card went up -- the sandbox's are per session, so a later one used to ride
along on an approval of something else.

Keyed by the call's tool name and arguments, the same exact-match key
APPROVE_FOR_SESSION already uses for a call with no structured permission (see
``session_approvals.exact_command_permission_id``), because that is what the
person approved: this call, with these arguments.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.session_approvals import (
    clear_session_refusals,
    denied_permissions,
    grant_offer,
    has_session_approval,
    record_card,
    record_denial,
    record_session_approval,
    revoke_session_approval,
    session_refusals,
    shown_card,
)
from app.modules.agent.domain.context import ToolContext


async def remember_denial(deps: ToolContext, result: object) -> None:
    """Record the refusal a ``needs_approval`` result carries, if it carries one.

    Takes the result rather than the error so every producer -- the pod tools,
    ``view_image``, ``ask_teammate`` -- records exactly the envelope the model is
    handed, and the key the approval is later matched by is built from the same
    ``tool_name`` and ``args`` the model is told to re-issue.
    """
    # Lazy: ``authority`` imports the authorization service, which the tool
    # modules calling this are imported beneath.
    from app.modules.agent.infrastructure.tools.authority import workload_actor_id

    envelope = _approval_envelope(result)
    if envelope is None:
        return
    tool_name = envelope.get("tool_name")
    permission_ids = envelope.get("permission_ids")
    if not isinstance(tool_name, str) or not isinstance(permission_ids, list):
        return
    args = envelope.get("args")
    await record_denial(
        session_id=str(deps.conversation_id),
        workload_actor_id=workload_actor_id(deps),
        tool_name=tool_name,
        args=dict(args) if isinstance(args, Mapping) else {},
        permission_ids=[str(p) for p in permission_ids],
    )


async def refused_permissions(
    *, conversation_id: object, workload_actor_id: str, tool_args: Mapping
) -> frozenset[str]:
    """The permissions the server refused the call a ``request_approval`` wraps.

    ``tool_args`` are the ``request_approval`` arguments; only the wrapped
    call's ``tool_name`` and ``args`` are read from them, to find the record.
    Its ``permission_ids`` are ignored -- that is the point.
    """
    tool_name = tool_args.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        return frozenset()
    args = tool_args.get("args")
    return await denied_permissions(
        session_id=str(conversation_id),
        workload_actor_id=workload_actor_id,
        tool_name=tool_name,
        args=dict(args) if isinstance(args, Mapping) else {},
    )


#: Tools that run in the sandbox, whose ``lemma`` calls reach the API with the
#: workload's own token. What they are refused is recorded per session by the
#: API's error handler, not per call here: a shell command is not the request
#: it makes.
SANDBOX_COMMAND_TOOLS = frozenset({"exec_command", "execute_python"})


async def sandbox_refusals(
    *, conversation_id: UUID, person_user_id: UUID, workload_actor_id: str
) -> frozenset[str]:
    """What this conversation's sandbox was refused, for this person and agent."""
    return await session_refusals(
        session_id=str(conversation_id),
        person_user_id=person_user_id,
        workload_actor_id=workload_actor_id,
    )


async def answered(
    *,
    conversation_id: UUID,
    person_user_id: UUID,
    workload_actor_id: str,
    permission_ids: frozenset[str],
) -> None:
    """Forget sandbox refusals an approval has answered (see
    ``session_approvals.clear_session_refusals``)."""
    if permission_ids:
        await clear_session_refusals(
            session_id=str(conversation_id),
            person_user_id=person_user_id,
            workload_actor_id=workload_actor_id,
            permission_ids=permission_ids,
        )


@asynccontextmanager
async def lent_to_the_sandbox(
    *,
    conversation_id: UUID,
    person_user_id: UUID,
    workload_actor_id: str,
    permission_ids: frozenset[str],
) -> AsyncIterator[None]:
    """Lend ``permission_ids`` to the sandbox's token for one approved command.

    The command's ``lemma`` calls are separate requests, authorised with the
    workload's token, so the only thing they can read an approval from is the
    session-approval store. The loan is written there for the length of the
    command and withdrawn after it -- only the entries this wrote, so an
    APPROVE_FOR_SESSION recorded for the same permission stands.

    The command used to run with a token minted for the person instead: an
    approved ``lemma pods delete`` carried everything the person could do, for
    as long as that shell lived (H7).
    """
    key = {
        "session_id": str(conversation_id),
        "person_user_id": person_user_id,
        "workload_actor_id": workload_actor_id,
    }
    added = [
        permission_id
        for permission_id in sorted(permission_ids)
        if not await has_session_approval(permission_id=permission_id, **key)
    ]
    for permission_id in added:
        await record_session_approval(permission_id=permission_id, **key)
    try:
        yield
    finally:
        for permission_id in added:
            await revoke_session_approval(permission_id=permission_id, **key)
        await answered(
            conversation_id=conversation_id,
            person_user_id=person_user_id,
            workload_actor_id=workload_actor_id,
            permission_ids=permission_ids,
        )


@dataclass(frozen=True, slots=True)
class ShownCard:
    """What a card put in front of its person: all an answer to it may do."""

    #: The refusals pending when the card was shown, lent on approval.
    permission_ids: frozenset[str] = frozenset()
    #: A standing grant approving adds (``connectors/grant_offer``), or None.
    grant: Mapping | None = None


async def pending_refusals(
    *,
    conversation_id: UUID,
    person_user_id: UUID | None,
    workload_actor_id: str,
    tool_name: str,
    args: Mapping | None,
) -> frozenset[str]:
    """Everything the call ``request_approval`` wraps is refused for now: its
    own refusals, and for a sandbox command what the session's sandbox was."""
    refused = await refused_permissions(
        conversation_id=conversation_id,
        workload_actor_id=workload_actor_id,
        tool_args={"tool_name": tool_name, "args": dict(args or {})},
    )
    if tool_name not in SANDBOX_COMMAND_TOOLS or person_user_id is None:
        return refused
    return refused | await sandbox_refusals(
        conversation_id=conversation_id,
        person_user_id=person_user_id,
        workload_actor_id=workload_actor_id,
    )


async def show_card(
    *,
    conversation_id: UUID,
    approval_id: str,
    person_user_id: UUID,
    workload_actor_id: str,
    tool_name: str,
    args: Mapping | None,
) -> ShownCard:
    """Write on the approval what its card shows: the refusals pending now.

    Called as the run pauses for the card. What is refused afterwards is not
    on it, and an answer to it never lends that (H6).
    """
    permission_ids = await pending_refusals(
        conversation_id=conversation_id,
        person_user_id=person_user_id,
        workload_actor_id=workload_actor_id,
        tool_name=tool_name,
        args=args,
    )
    offer = await grant_offer(
        session_id=str(conversation_id),
        workload_actor_id=workload_actor_id,
        tool_name=tool_name,
        args=dict(args or {}),
    )
    await record_card(
        session_id=str(conversation_id),
        approval_id=approval_id,
        card={
            "person_user_id": str(person_user_id),
            "workload_actor_id": workload_actor_id,
            "tool_name": tool_name,
            "permission_ids": sorted(permission_ids),
            "grant": offer,
        },
    )
    return ShownCard(permission_ids=permission_ids, grant=offer)


async def card_lends(
    *,
    conversation_id: UUID,
    approval_id: str | None,
    person_user_id: UUID,
    workload_actor_id: str,
    tool_name: str,
) -> ShownCard:
    """What answering this approval's card may do: what the card showed.

    Nothing when no card was recorded, or it was shown to someone else, for
    another agent or another call -- each of those lends nothing rather than
    guess, and the call is refused and asks again.
    """
    if not approval_id:
        return ShownCard()
    card = await shown_card(session_id=str(conversation_id), approval_id=approval_id)
    if (
        card.get("person_user_id") != str(person_user_id)
        or card.get("workload_actor_id") != workload_actor_id
        or card.get("tool_name") != tool_name
    ):
        return ShownCard()
    permission_ids = card.get("permission_ids")
    grant = card.get("grant")
    return ShownCard(
        permission_ids=frozenset(
            p for p in permission_ids or [] if isinstance(p, str) and p
        )
        if isinstance(permission_ids, list)
        else frozenset(),
        grant=grant if isinstance(grant, Mapping) else None,
    )


def _approval_envelope(result: object) -> Mapping | None:
    if isinstance(result, Mapping):
        needs, envelope = result.get("needs_approval"), result.get("approval")
    else:
        needs = getattr(result, "needs_approval", False)
        envelope = getattr(result, "approval", None)
    if not needs or not isinstance(envelope, Mapping):
        return None
    return envelope


__all__ = [
    "SANDBOX_COMMAND_TOOLS",
    "ShownCard",
    "answered",
    "card_lends",
    "lent_to_the_sandbox",
    "pending_refusals",
    "refused_permissions",
    "remember_denial",
    "sandbox_refusals",
    "show_card",
]
