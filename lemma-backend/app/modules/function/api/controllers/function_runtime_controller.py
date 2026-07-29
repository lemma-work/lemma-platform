"""Authenticated internal callback surface for function sandbox runners."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response

from app.modules.function.api.dependencies import FunctionRuntimeGatewayDep
from app.modules.function.application.function_runtime_gateway import (
    RuntimeArtifactCorrupt,
    RuntimeCredentialRejected,
    RuntimeStateRejected,
)
from app.core.authorization.delegation import WorkloadPrincipalType
from app.modules.function.domain.entities import FunctionSessionPrincipal
from app.modules.function.contracts.runtime import (
    RuntimeEventResponse,
    RuntimeTerminalRequest,
)


router = APIRouter(
    prefix="/internal/function-runtime",
    tags=["Function runtime"],
    include_in_schema=False,
)


def _principal(request: Request) -> FunctionSessionPrincipal:
    user = getattr(request.state, "user", None)
    claims = getattr(request.state, "delegation_claims", None)
    if (
        user is None
        or claims is None
        or claims.actor_type != WorkloadPrincipalType.FUNCTION
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return FunctionSessionPrincipal(
        user_id=user.id,
        pod_id=claims.pod_id,
        function_id=claims.actor_id,
        session_id=claims.session_id,
        actor_name=claims.actor_name,
        scope=tuple(claims.scope),
    )


@router.get(
    "/functions/{function_id}/artifacts/{revision_hash}",
    response_class=Response,
)
async def download_definition_artifact(
    function_id: UUID,
    revision_hash: str,
    request: Request,
    gateway: FunctionRuntimeGatewayDep,
) -> Response:
    if not revision_hash.startswith("sha256:") or len(revision_hash) != 71:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="revision_hash must be an exact sha256 digest",
        )
    try:
        data = await gateway.definition_artifact(
            function_id,
            revision_hash,
            _principal(request),
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
    http_request: Request,
    gateway: FunctionRuntimeGatewayDep,
) -> RuntimeEventResponse:
    try:
        return await gateway.terminal(run_id, _principal(http_request), request)
    except RuntimeCredentialRejected as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from exc
    except RuntimeStateRejected as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT) from exc
