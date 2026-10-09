"""The credential of a function run that acts for nobody in the pod.

A member's function run calls back into the API with a session token minted
for that member, and is authorized as the member's access intersected with the
function's grants. A run started for a contact has no member to mint for. The
person it serves is outside the pod and holds nothing, and lending it the
function owner's session would let a stranger's request reach whatever the
owner can. So such a run carries this instead: a signed token naming the run,
the function, the pod and the contact, and nothing a session could be built
from.

The context built from it is the function's own workload (``ActorType.FUNCTION``)
with no invoking person. That means the function's grants, pinned to its pod,
with ``contact_id`` set so the pod database shows contact-owned tables only as
that contact's rows. No user stands behind it, so routes are refused unless
they work on the pod's data or serve the run itself (``allows_path``).

The ``lfr_`` prefix lets ``verify_auth`` recognise the token without asking the
session provider about it first, which would only call it malformed.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from app.core.authorization.context import Context
from app.core.authorization.delegation_revocation import is_delegation_revoked
from app.core.authorization.service import AuthorizationDataService
from app.core.crypto.tokens import (
    InvalidSignedToken,
    mint_signed_token,
    verify_signed_token,
)
from app.core.domain.entity import AuthenticatedPrincipal

__all__ = [
    "FUNCTION_RUN_TOKEN_PREFIX",
    "NOBODY_USER_ID",
    "FunctionRunClaims",
    "InvalidSignedToken",
    "admit_function_run",
    "allows_path",
    "build_function_run_context",
    "function_run_bearer",
    "is_function_run_token",
    "mint_function_run_token",
    "parse_function_run_token",
]

FUNCTION_RUN_TOKEN_PREFIX = "lfr_"
_PURPOSE = "function-run"
_WORKLOAD = "function"

#: The user id a route sees on a person-less run. No row is stamped with it,
#: so per-member row security matches nothing, the way it does for a
#: contact's own reads (``datastore/infrastructure/contact_rows.py``).
NOBODY_USER_ID = UUID(int=0)

#: Routes a person-less run may call, after ``/pods/{its pod}``. The pod's data
#: is what a function works on. Anything that acts as a member (starting
#: other runs, messaging people, the member's own settings) needs a person
#: this run does not have.
_POD_ROUTE_PREFIXES = ("/datastore/",)
#: The runtime's own callbacks: fetching the function's code, reporting its end.
_RUNTIME_PREFIX = "/internal/function-runtime/"


@dataclass(frozen=True, slots=True)
class FunctionRunClaims:
    """What a function-run token vouches for."""

    run_id: UUID
    function_id: UUID
    pod_id: UUID
    revision_hash: str
    #: ``None`` for a run started by an anonymous form submission.
    contact_id: UUID | None

    @property
    def actor_label(self) -> str:
        """Who the run acts for, as the audit trail names them."""
        return f"contact:{self.contact_id}" if self.contact_id else "anonymous"


def mint_function_run_token(claims: FunctionRunClaims, *, ttl_seconds: int) -> str:
    """A bearer token for one person-less run, valid for ``ttl_seconds``."""
    return FUNCTION_RUN_TOKEN_PREFIX + mint_signed_token(
        _PURPOSE,
        {
            "workload": _WORKLOAD,
            "run_id": str(claims.run_id),
            "function_id": str(claims.function_id),
            "pod_id": str(claims.pod_id),
            "revision_hash": claims.revision_hash,
            "contact_id": str(claims.contact_id) if claims.contact_id else None,
        },
        ttl_seconds=ttl_seconds,
    )


def is_function_run_token(token: str) -> bool:
    return token.startswith(FUNCTION_RUN_TOKEN_PREFIX)


def parse_function_run_token(token: str) -> FunctionRunClaims:
    """The claims of a valid, unexpired function-run token.

    Raises ``InvalidSignedToken`` for anything else, including a well-signed
    token whose claims are not the shape minted above.
    """
    if not is_function_run_token(token):
        raise InvalidSignedToken("not a function-run token")
    raw = verify_signed_token(_PURPOSE, token.removeprefix(FUNCTION_RUN_TOKEN_PREFIX))
    if raw.get("workload") != _WORKLOAD:
        raise InvalidSignedToken("not a function workload")
    try:
        contact = raw.get("contact_id")
        return FunctionRunClaims(
            run_id=UUID(str(raw["run_id"])),
            function_id=UUID(str(raw["function_id"])),
            pod_id=UUID(str(raw["pod_id"])),
            revision_hash=str(raw["revision_hash"]),
            contact_id=UUID(str(contact)) if contact else None,
        )
    except (KeyError, ValueError) as exc:
        raise InvalidSignedToken("malformed function-run claims") from exc


def allows_path(claims: FunctionRunClaims, path: str) -> bool:
    """Whether a person-less run may call ``path`` at all.

    Pinned to the run's pod by the path itself, before any route resolves a
    context: a token for one pod never reaches another's data.
    """
    if path.startswith(_RUNTIME_PREFIX):
        return True
    pod_prefix = f"/pods/{claims.pod_id}"
    if not path.startswith(pod_prefix):
        return False
    rest = path.removeprefix(pod_prefix)
    return rest.startswith(_POD_ROUTE_PREFIXES)


def function_run_bearer(connection: HTTPConnection) -> str | None:
    """The bearer, when it is a function-run token rather than a session."""
    scheme, _, value = connection.headers.get("authorization", "").partition(" ")
    token = value.strip()
    if scheme.lower() != "bearer" or not is_function_run_token(token):
        return None
    return token


async def admit_function_run(connection: HTTPConnection, token: str) -> None:
    """Admit a person-less run to the request, or refuse it.

    The run has no user, so the routes it may call are limited before any of
    them runs, and the user id they see is nobody's. The context itself is
    built where every other one is (``authorization/dependencies``), from
    ``function_run_claims``.
    """
    try:
        claims = parse_function_run_token(token)
    except InvalidSignedToken as exc:
        raise HTTPException(status_code=401, detail="Unauthorized") from exc
    if not allows_path(claims, connection.url.path):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "FUNCTION_RUN_ROUTE_NOT_ALLOWED",
                "message": (
                    "This function run acts for no member of the pod, and "
                    "this route needs one."
                ),
            },
        )
    # The same revocation a delegated token honours: deleting the function
    # ends what its in-flight runs may do, not only what new ones may.
    if await is_delegation_revoked(actor_id=claims.function_id):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "DELEGATION_REVOKED",
                "message": "Delegated workload has been revoked.",
            },
        )
    connection.state.user = AuthenticatedPrincipal(id=NOBODY_USER_ID)
    connection.state.session = None
    connection.state.auth_claims = {}
    connection.state.delegation_claims = None
    connection.state.function_run_claims = claims


async def build_function_run_context(
    session: AsyncSession,
    claims: FunctionRunClaims,
    *,
    request_id: str | None = None,
) -> Context:
    """The function's own authority, for the contact the run serves.

    No user on it: ``workload_authority`` and the authorizer read that as
    grants only, with no person's access to intersect.
    """
    ctx = await AuthorizationDataService(session).build_workload_context(
        principal_type="FUNCTION",
        principal_id=claims.function_id,
        pod_id=claims.pod_id,
        request_id=request_id,
    )
    ctx.contact_id = claims.contact_id
    return ctx
