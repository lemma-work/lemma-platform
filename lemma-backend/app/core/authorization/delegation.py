"""Delegated authorization token claims and workload delegation constants.

Delegation-scope convention: contexts minted for a single operation carry a
minimal scope (e.g. a function tool call sets ``{function.execute}``; the
scope check is implication-expanded, so the implied ``function.read`` is
admitted too). Long-lived agent contexts (sub-agent runs, pod data-access
tools) are deliberately unscoped — the workload's explicit resource grants
and the destructive-action gate are the limiters there.

The pod-default agent's two names also live here, together and on purpose.
``DEFAULT_POD_AGENT_NAME`` is what the row carries and what a token claims;
``DEFAULT_RESPONDER_NAME`` is what a person reads. They were apart, and the
display name lost: the change that gave the pod's agent a real row normalised
every place that had faked one to the *product* name, because nothing beside the
wire value said the actor already had a name of its own.
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.authorization.permissions import Permissions


CLAIM_ACTOR_TYPE = "gpy_actor_type"
CLAIM_ACTOR_ID = "gpy_actor_id"
CLAIM_ACTOR_NAME = "gpy_actor_name"
CLAIM_POD_ID = "gpy_pod_id"
CLAIM_SESSION_ID = "gpy_session_id"
CLAIM_SCOPE = "gpy_scope"
CLAIM_INVOKED_BY_USER_ID = "gpy_invoked_by_user_id"
CLAIM_DELEGATION_VERSION = "gpy_delegation_version"

DELEGATION_VERSION = 1
DEFAULT_POD_AGENT_ID = UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_POD_AGENT_NAME = "pod_default"
POD_DEFAULT_AGENT_SELECTOR = "POD_DEFAULT"
POD_DEFAULT_AGENT_SELECTOR_ALIASES = frozenset(
    {POD_DEFAULT_AGENT_SELECTOR, DEFAULT_POD_AGENT_NAME}
)

# What the pod's own agent is called wherever a person can read it. See
# `lemma-frontend/src/data/agent-names.ts` — these two must agree, because someone
# reading a name in Slack and someone reading it in the app are reading about
# the same actor.
DEFAULT_RESPONDER_NAME = "Lem"


def agent_display_name(name: str | None) -> str:
    """An agent's row name → what a person reads.

    The pod's own agent is stored as ``pod_default`` and claimed as
    ``POD_DEFAULT``; both are identifiers, and neither is a name to show
    anybody. ``None`` resolves the same way, because a surface with no agent
    is the pod answering — the same reading :func:`is_pod_default_agent`
    gives a null agent id.

    Display only. Anything that resolves a name back to a row — conversation
    creation, address allocation — needs the stored value and must not call
    this.
    """
    if not name or name in POD_DEFAULT_AGENT_SELECTOR_ALIASES:
        return DEFAULT_RESPONDER_NAME
    return name


def is_pod_default_agent(agent_id: UUID | None, *, pod_id: UUID | None) -> bool:
    """Whether this id names the pod's own assistant.

    Three shapes answer yes, and they are three eras rather than three cases:

    * ``pod_id`` -- the assistant's row, whose id *is* its pod's.
    * ``None`` -- every conversation written before that row existed, and
      anything an older process is still writing during a rolling deploy.
    * ``DEFAULT_POD_AGENT_ID`` -- the sentinel the assistant was named by in
      delegation tokens, which are signed and outlive the deploy that stopped
      issuing them.

    The null arm is not a transition measure to be tidied away later. It is
    what makes an accidental loss of the row degrade to *correct*: the foreign
    key from a conversation is ``ON DELETE SET NULL``, and a null still reads
    as the assistant here rather than as a deleted named agent.

    Deliberately free of I/O. The per-request check in
    ``app/core/authorization/dependencies.py`` runs this on a token's claims,
    and answering it with a row lookup would put a query on every request.
    """
    if agent_id is None:
        return True
    if agent_id == DEFAULT_POD_AGENT_ID:
        return True
    return pod_id is not None and agent_id == pod_id


def effective_agent_id(agent_id: UUID | None, *, pod_id: UUID) -> UUID:
    """The id this agent is known by, with the assistant's absences normalised.

    Use when comparing two agent references that may have been written in
    different eras -- a conversation backfilled to ``pod_id`` against a surface
    route still holding ``None`` means the same agent, and comparing them raw
    says they differ.
    """
    return pod_id if is_pod_default_agent(agent_id, pod_id=pod_id) else agent_id


# No workload — the default pod agent included — performs these by default.
# A workload needs either an explicit grant of the destructive permission
# (standing authority; keeps headless schedules/functions working) or a live
# session approval from the user (APPROVE_FOR_SESSION, Redis-backed, TTL).
# Deleting a table's rows is in it (audit H14): one call empties a table, and
# writing a row is not consent to that. File deletes through folder.write are
# not -- a file is replaced as often as it is removed. Changing the pod's
# connectors is in it: removing one stops everything that runs through it,
# and pointing a SHARED one at an account decides whose credential the pod's
# calls go out under.
DESTRUCTIVE_ACTIONS = {
    Permissions.POD_DELETE,
    Permissions.POD_ROLE_MANAGE,
    Permissions.POD_MEMBER_MANAGE,
    Permissions.DATASTORE_TABLE_DELETE,
    Permissions.DATASTORE_RECORD_DELETE,
    Permissions.FOLDER_DELETE,
    Permissions.FUNCTION_DELETE,
    Permissions.AGENT_DELETE,
    Permissions.WORKFLOW_DELETE,
    Permissions.APP_DELETE,
    Permissions.SCHEDULE_DELETE,
    Permissions.POD_CONNECTOR_MANAGE,
}


class WorkloadPrincipalType(str, Enum):
    AGENT = "AGENT"
    FUNCTION = "FUNCTION"

    @classmethod
    def _missing_(cls, value: object) -> "WorkloadPrincipalType | None":
        # Case-insensitive: tolerate any lingering lowercase input (e.g. an
        # in-flight delegation token minted before the CAPS standardization).
        if not isinstance(value, str):
            return None
        normalized = value.strip().upper()
        for member in cls:
            if member.value == normalized:
                return member
        return None


class DelegationClaims(BaseModel):
    actor_type: WorkloadPrincipalType
    actor_id: UUID
    actor_name: str | None = None
    pod_id: UUID
    session_id: str
    scope: list[str] = Field(default_factory=list)
    invoked_by_user_id: UUID
    delegation_version: int


def is_default_pod_agent_claims(claims: DelegationClaims | None) -> bool:
    """Whether this token was minted for the pod's own assistant.

    The one place the answer comes from a token rather than a row: this runs on
    every delegated request, and ``is_pod_default_agent`` is deliberately free
    of I/O so it need not become a query.

    Both claims have to agree. The id arm alone would promote any workload
    whose id happened to equal its pod's, and what turns on this is whether a
    token acts with its user's permissions or only its own grants.
    """
    return (
        claims is not None
        and is_pod_default_agent(claims.actor_id, pod_id=claims.pod_id)
        and claims.actor_name in {None, DEFAULT_POD_AGENT_NAME}
    )


class DelegationClaimsError(ValueError):
    """Raised when delegated auth claims are invalid."""


def _coerce_scope(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str) and value.strip():
        return [value]
    if isinstance(value, list) and all(
        isinstance(v, str) and v.strip() for v in value
    ):
        return value
    raise DelegationClaimsError("Invalid delegated scope")


def parse_delegation_claims(payload: dict[str, Any]) -> DelegationClaims | None:
    actor_type = payload.get(CLAIM_ACTOR_TYPE)
    actor_id = payload.get(CLAIM_ACTOR_ID)

    if actor_type is None and actor_id is None:
        # A plain person token has none of the delegation namespace. If any
        # companion claim is present, do not silently downgrade a partial
        # delegated token to a person context.
        companion_claims = {
            CLAIM_ACTOR_NAME,
            CLAIM_POD_ID,
            CLAIM_SESSION_ID,
            CLAIM_SCOPE,
            CLAIM_INVOKED_BY_USER_ID,
            CLAIM_DELEGATION_VERSION,
        }
        if companion_claims.intersection(payload):
            raise DelegationClaimsError("Incomplete delegated claims")
        return None

    required = {
        CLAIM_ACTOR_TYPE,
        CLAIM_ACTOR_ID,
        CLAIM_POD_ID,
        CLAIM_SESSION_ID,
        CLAIM_INVOKED_BY_USER_ID,
    }
    missing = [key for key in required if payload.get(key) in (None, "")]
    if missing:
        raise DelegationClaimsError(f"Missing delegated claim(s): {', '.join(missing)}")

    try:
        parsed_actor_type = WorkloadPrincipalType(actor_type)
    except Exception as exc:
        raise DelegationClaimsError("Unsupported delegated actor type") from exc

    try:
        parsed_actor_id = UUID(str(actor_id))
        parsed_pod_id = UUID(str(payload[CLAIM_POD_ID]))
        invoked_by_user_id = UUID(str(payload[CLAIM_INVOKED_BY_USER_ID]))
    except Exception as exc:
        raise DelegationClaimsError("Invalid UUID in delegated claims") from exc

    session_id = payload[CLAIM_SESSION_ID]
    if not isinstance(session_id, str) or not session_id.strip():
        raise DelegationClaimsError("Invalid delegated session id")

    actor_name_raw = payload.get(CLAIM_ACTOR_NAME)
    if actor_name_raw not in (None, "") and not isinstance(actor_name_raw, str):
        raise DelegationClaimsError("Invalid delegated actor name")
    if isinstance(actor_name_raw, str) and actor_name_raw and not actor_name_raw.strip():
        raise DelegationClaimsError("Invalid delegated actor name")

    scope = _coerce_scope(payload.get(CLAIM_SCOPE))
    version = payload.get(CLAIM_DELEGATION_VERSION, DELEGATION_VERSION)
    # bool is an int subclass in Python, and int(1.9) silently truncates.
    # Delegation versions are protocol identifiers, not values to coerce.
    if type(version) is not int:
        raise DelegationClaimsError("Invalid delegation version")

    if version != DELEGATION_VERSION:
        raise DelegationClaimsError("Unsupported delegation version")

    return DelegationClaims(
        actor_type=parsed_actor_type,
        actor_id=parsed_actor_id,
        actor_name=actor_name_raw or None,
        pod_id=parsed_pod_id,
        session_id=session_id,
        scope=scope,
        invoked_by_user_id=invoked_by_user_id,
        delegation_version=version,
    )
