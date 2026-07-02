"""Delegated authorization token claims and workload delegation constants.

Delegation-scope convention: contexts minted for a single operation carry a
minimal scope (e.g. a function tool call sets ``{function.execute}``; the
scope check is implication-expanded, so the implied ``function.read`` is
admitted too). Long-lived agent contexts (sub-agent runs, pod data-access
tools) are deliberately unscoped — the workload's explicit resource grants
and the destructive-action gate are the limiters there.
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

# No workload — the default pod agent included — performs these by default.
# A workload needs either an explicit grant of the destructive permission
# (standing authority; keeps headless schedules/functions working) or a live
# session approval from the user (APPROVE_FOR_SESSION, Redis-backed, TTL).
# Data-level deletes (records via datastore.record.write, file deletes via
# folder.write) are deliberately NOT in this set — they are routine
# automation, scoped by RLS. Note connector_account.manage implies
# connector_account.use; the gate applies to manage checks only.
DESTRUCTIVE_ACTIONS = {
    Permissions.POD_DELETE,
    Permissions.POD_ROLE_MANAGE,
    Permissions.POD_MEMBER_MANAGE,
    Permissions.DATASTORE_TABLE_DELETE,
    Permissions.FOLDER_DELETE,
    Permissions.FUNCTION_DELETE,
    Permissions.AGENT_DELETE,
    Permissions.WORKFLOW_DELETE,
    Permissions.APP_DELETE,
    Permissions.SCHEDULE_DELETE,
    Permissions.CONNECTOR_ACCOUNT_MANAGE,
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


class DelegationClaimsError(ValueError):
    """Raised when delegated auth claims are invalid."""


def _coerce_scope(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value
    raise DelegationClaimsError("Invalid delegated scope")


def parse_delegation_claims(payload: dict[str, Any]) -> DelegationClaims | None:
    actor_type = payload.get(CLAIM_ACTOR_TYPE)
    actor_id = payload.get(CLAIM_ACTOR_ID)

    if actor_type is None and actor_id is None:
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

    scope = _coerce_scope(payload.get(CLAIM_SCOPE))
    version_raw = payload.get(CLAIM_DELEGATION_VERSION, DELEGATION_VERSION)
    try:
        version = int(version_raw)
    except Exception as exc:
        raise DelegationClaimsError("Invalid delegation version") from exc

    if version != DELEGATION_VERSION:
        raise DelegationClaimsError("Unsupported delegation version")

    return DelegationClaims(
        actor_type=parsed_actor_type,
        actor_id=parsed_actor_id,
        actor_name=(
            str(payload[CLAIM_ACTOR_NAME])
            if payload.get(CLAIM_ACTOR_NAME) not in (None, "")
            else None
        ),
        pod_id=parsed_pod_id,
        session_id=str(payload[CLAIM_SESSION_ID]),
        scope=scope,
        invoked_by_user_id=invoked_by_user_id,
        delegation_version=version,
    )
