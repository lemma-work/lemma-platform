"""Authenticated internal callback surface for function sandbox runners."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.modules.function.api.dependencies import FunctionRuntimeGatewayDep
from app.modules.function.application.function_runtime_gateway import (
    RuntimeArtifactCorrupt,
    RuntimeCredentialRejected,
    RuntimeStateRejected,
)
from app.core.authorization.delegation import WorkloadPrincipalType
from app.modules.function.domain.entities import FunctionSessionPrincipal
from app.modules.function.contracts.runtime import (
    RuntimeClaimRequest,
    RuntimeClaimResponse,
    RuntimeEventResponse,
    RuntimeTerminalRequest,
)


router = APIRouter(
    prefix="/internal/function-runtime",
    tags=["Function runtime"],
    include_in_schema=False,
)
bearer = HTTPBearer(auto_error=False)


def _credential(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return credentials.credentials


def _revision_hash(request: Request) -> str:
    value = request.headers.get("if-match", "").strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    if not value.startswith("sha256:") or len(value) != 71:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="If-Match must contain an exact sha256 revision hash",
        )
    return value


@router.post("/runs/{run_id}:claim", response_model=RuntimeClaimResponse)
async def claim_run(
    run_id: UUID,
    request: RuntimeClaimRequest,
    http_request: Request,
    gateway: FunctionRuntimeGatewayDep,
    credential: str = Depends(_credential),
) -> RuntimeClaimResponse:
    user = getattr(http_request.state, "user", None)
    claims = getattr(http_request.state, "delegation_claims", None)
    if (
        user is None
        or claims is None
        or claims.actor_type != WorkloadPrincipalType.FUNCTION
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    principal = FunctionSessionPrincipal(
        user_id=user.id,
        pod_id=claims.pod_id,
        function_id=claims.actor_id,
        session_id=claims.session_id,
    )
    try:
        return await gateway.claim(credential, principal, run_id, request)
    except RuntimeCredentialRejected as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from exc
    except RuntimeStateRejected as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT) from exc


@router.get("/runs/{run_id}/artifact", response_class=Response)
async def download_artifact(
    run_id: UUID,
    gateway: FunctionRuntimeGatewayDep,
    credential: str = Depends(_credential),
) -> Response:
    try:
        data = await gateway.artifact(run_id, credential)
    except RuntimeCredentialRejected as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from exc
    except RuntimeArtifactCorrupt as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from exc
    return Response(content=data, media_type="application/zip")


@router.get("/functions/{function_id}/artifact", response_class=Response)
async def download_definition_artifact(
    function_id: UUID,
    request: Request,
    gateway: FunctionRuntimeGatewayDep,
    credential: str = Depends(_credential),
) -> Response:
    try:
        data = await gateway.definition_artifact(
            function_id,
            _revision_hash(request),
            credential,
        )
    except RuntimeCredentialRejected as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from exc
    except (FileNotFoundError, RuntimeArtifactCorrupt) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE
        ) from exc
    return Response(content=data, media_type="application/zip")


@router.post(
    "/runs/{run_id}:terminal", response_model=RuntimeEventResponse
)
async def report_terminal(
    run_id: UUID,
    request: RuntimeTerminalRequest,
    gateway: FunctionRuntimeGatewayDep,
    credential: str = Depends(_credential),
) -> RuntimeEventResponse:
    try:
        return await gateway.terminal(run_id, credential, request)
    except RuntimeCredentialRejected as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from exc
    except RuntimeStateRejected as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT) from exc
