"""Authenticated internal callback surface for function sandbox runners."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.modules.function.api.dependencies import FunctionRuntimeGatewayDep
from app.modules.function.application.function_runtime_gateway import (
    RuntimeArtifactCorrupt,
    RuntimeCredentialRejected,
    RuntimeFenceRejected,
)
from app.modules.function.contracts.runtime import (
    RuntimeClaimRequest,
    RuntimeClaimResponse,
    RuntimeEventResponse,
    RuntimeStartedRequest,
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


@router.post("/attempts:claim", response_model=RuntimeClaimResponse)
async def claim_attempt(
    request: RuntimeClaimRequest,
    gateway: FunctionRuntimeGatewayDep,
    credential: str = Depends(_credential),
) -> RuntimeClaimResponse:
    try:
        return await gateway.claim(credential, request)
    except RuntimeCredentialRejected as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from exc
    except RuntimeFenceRejected as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT) from exc


@router.get("/attempts/{attempt_id}/artifact", response_class=Response)
async def download_artifact(
    attempt_id: UUID,
    gateway: FunctionRuntimeGatewayDep,
    credential: str = Depends(_credential),
) -> Response:
    try:
        data = await gateway.artifact(attempt_id, credential)
    except RuntimeCredentialRejected as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from exc
    except RuntimeArtifactCorrupt as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from exc
    return Response(content=data, media_type="application/zip")


@router.post(
    "/attempts/{attempt_id}:started", response_model=RuntimeEventResponse
)
async def report_started(
    attempt_id: UUID,
    request: RuntimeStartedRequest,
    gateway: FunctionRuntimeGatewayDep,
    credential: str = Depends(_credential),
) -> RuntimeEventResponse:
    try:
        return await gateway.started(attempt_id, credential, request)
    except RuntimeCredentialRejected as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from exc
    except RuntimeFenceRejected as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT) from exc


@router.post(
    "/attempts/{attempt_id}:terminal", response_model=RuntimeEventResponse
)
async def report_terminal(
    attempt_id: UUID,
    request: RuntimeTerminalRequest,
    gateway: FunctionRuntimeGatewayDep,
    credential: str = Depends(_credential),
) -> RuntimeEventResponse:
    try:
        return await gateway.terminal(attempt_id, credential, request)
    except RuntimeCredentialRejected as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from exc
    except RuntimeFenceRejected as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT) from exc
