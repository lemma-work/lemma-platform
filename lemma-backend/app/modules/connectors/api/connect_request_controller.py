from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

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
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"


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


def _render_callback_page(
    *,
    status: str,
    eyebrow: str,
    title: str,
    message: str,
    detail_label: str,
    detail_value: str,
    status_code: int = 200,
) -> HTMLResponse:
    body = (TEMPLATES_DIR / "oauth_callback_result.html").read_text(encoding="utf-8")
    return HTMLResponse(
        content=body.format(
            status=escape(status, quote=True),
            eyebrow=escape(eyebrow, quote=True),
            title=escape(title, quote=True),
            message=escape(message, quote=True),
            detail_label=escape(detail_label, quote=True),
            detail_value=escape(detail_value, quote=True),
        ),
        status_code=status_code,
    )


@router.get(
    "/oauth/callback",
    operation_id="connector.oauth.callback",
    summary="OAuth Callback",
    description="Handle OAuth callback and complete account connection. Requires an authenticated session; the callback is bound to the user who initiated the connect request.",
    response_class=HTMLResponse,
    response_model=None,
)
async def oauth_callback(
    request: Request,
    user: CurrentUser,
    connector_service: ConnectorServiceDep,
    error: Optional[str] = Query(default=None),
    response_format: Optional[str] = Query(default=None, alias="format"),
) -> Response:
    wants_json = _wants_json(request, response_format)

    if error:
        if wants_json:
            return JSONResponse(
                status_code=400,
                content={
                    "code": "OAUTH_PROVIDER_ERROR",
                    "message": f"OAuth error: {error}",
                },
            )
        return _render_callback_page(
            status="failed",
            eyebrow="Connection failed",
            title="We could not connect your account.",
            message="The provider did not complete the authorization. You can close this tab and try connecting again from Lemma.",
            detail_label="Provider response",
            detail_value=error,
            status_code=400,
        )

    redirect_uri = str(request.url)
    state = request.query_params.get("state")
    # Log a redacted view only: never include the full ``state`` (it is the
    # bearer credential that completes the connect) and never log
    # ``redirect_uri`` verbatim (it carries the OAuth ``code``).
    request_id = request.headers.get("x-request-id")
    logger.info(
        "OAuth callback received",
        state_prefix=state[:6] if state else None,
        has_code=bool(request.query_params.get("code")),
        request_id=request_id,
    )

    try:
        account = await connector_service.handle_oauth_callback(
            redirect_uri=redirect_uri,
            state=state,
            expected_user_id=user.id,
        )
    except ConnectorDomainError as exc:
        if wants_json:
            return JSONResponse(
                status_code=exc.status_code,
                content={"code": exc.code, "message": exc.message},
            )
        return _render_callback_page(
            status="failed",
            eyebrow="Connection failed",
            title="We could not connect your account.",
            message="The connection was not completed. You can close this tab and try again from Lemma.",
            detail_label="What happened",
            detail_value=exc.message,
            status_code=exc.status_code,
        )

    account_response = AccountResponseSchema.model_validate(account)
    account_response.provider = await connector_service.get_account_provider(account)
    if wants_json:
        return JSONResponse(content=account_response.model_dump(mode="json"))

    connector = await connector_service.get_connector(account.connector_id)
    app_label = connector.title or connector.id.replace("_", " ").replace("-", " ").title()
    detail_value = account.email or "Account connected"
    return _render_callback_page(
        status="success",
        eyebrow="Account connected",
        title=f"{app_label} is connected.",
        message="You can close this tab and return to Lemma. The account is ready to use in the workflows and pods where you allow it.",
        detail_label="Connected account",
        detail_value=detail_value,
    )
