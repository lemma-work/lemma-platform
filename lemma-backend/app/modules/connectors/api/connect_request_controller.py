from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.core.api.callback_page import (
    identity_html,
    message_html,
    render_callback_page,
    safe_provider_error,
    sentence,
)
from app.core.api.dependencies import CurrentUser
from app.modules.connectors.api.dependencies import ConnectorServiceDep
from app.modules.connectors.api.schemas import (
    AccountResponseSchema,
    ConnectRequestInitiateSchema,
    ConnectRequestResponseSchema,
)
from app.modules.connectors.domain.errors import ConnectorDomainError
from app.core.log.log import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/connectors/connect-requests", tags=["Connectors"])
org_router = APIRouter(
    prefix="/organizations/{organization_id}/connectors/connect-requests",
    tags=["Connectors"],
)


@org_router.post(
    "",
    response_model=ConnectRequestResponseSchema,
    operation_id="connector.connect_request.create",
    summary="Initiate Connect Request",
    description="Initiate an OAuth connection request for a connector",
)
async def initiate_connect_request(
    user: CurrentUser,
    organization_id: UUID,
    data: ConnectRequestInitiateSchema,
    connector_service: ConnectorServiceDep,
) -> ConnectRequestResponseSchema:
    connect_request = await connector_service.initiate_connect_request(
        user_id=user.id,
        organization_id=organization_id,
        connector_id=data.connector_id,
        auth_config_id=data.auth_config_id,
    )

    return ConnectRequestResponseSchema.model_validate(connect_request)


def _wants_json(request: Request, response_format: str | None) -> bool:
    if response_format and response_format.lower() == "json":
        return True

    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


@router.get(
    "/oauth/callback",
    operation_id="connector.oauth.callback",
    summary="OAuth Callback",
    description="Handle OAuth callback and complete account connection. This endpoint is public and uses state parameter for security.",
    response_class=HTMLResponse,
    response_model=None,
)
async def oauth_callback(
    request: Request,
    connector_service: ConnectorServiceDep,
    error: Optional[str] = Query(default=None),
    response_format: Optional[str] = Query(default=None, alias="format"),
) -> Response:
    wants_json = _wants_json(request, response_format)

    if error:
        # Never reflect the provider's string back verbatim. OAuth error codes
        # are a small set of tokens (`access_denied`, `invalid_scope`, ...), so
        # anything outside that shape is not information worth relaying and is
        # exactly what makes reflecting it a vulnerability.
        safe_error = safe_provider_error(error)
        if wants_json:
            return JSONResponse(
                status_code=400,
                content={
                    "code": "OAUTH_PROVIDER_ERROR",
                    "message": "The provider rejected the authorization.",
                    "provider_error": safe_error,
                },
            )
        return render_callback_page(
            succeeded=False,
            app_label="",
            icon=None,
            title="The account wasn’t connected",
            body_html=message_html(
                f"The provider ended the authorization with “{safe_error}”, "
                "so nothing was saved. You can start the connection again from Lemma."
            ),
            status_code=400,
        )

    redirect_uri = str(request.url)
    state = request.query_params.get("state")
    logger.debug("connectors.connect_request_controller.state.observed")

    try:
        account = await connector_service.handle_oauth_callback(
            redirect_uri=redirect_uri,
            state=state,
        )
    except ConnectorDomainError as exc:
        if wants_json:
            return JSONResponse(
                status_code=exc.status_code,
                content={"code": exc.code, "message": exc.message},
            )
        return render_callback_page(
            succeeded=False,
            app_label="",
            icon=None,
            title="The account wasn’t connected",
            body_html=message_html(
                f"{sentence(exc.message)} Nothing was saved — you can start the "
                "connection again from Lemma."
            ),
            status_code=exc.status_code,
        )

    account_response = AccountResponseSchema.model_validate(account)
    account_response.kind = await connector_service.get_account_kind(account)
    if wants_json:
        return JSONResponse(content=account_response.model_dump(mode="json"))

    connector = await connector_service.get_connector(account.connector_id)
    app_label = (
        connector.title or connector.id.replace("_", " ").replace("-", " ").title()
    )
    return render_callback_page(
        succeeded=True,
        app_label=app_label,
        icon=connector.icon,
        title=f"{app_label} is connected",
        body_html=identity_html(account.display_name, account.email),
    )
