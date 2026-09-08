from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.core.api.callback_page import (
    identity_html,
    message_html,
    next_step_html,
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
from app.modules.connectors.domain.account import AccountEntity
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


def _outstanding_install(account: AccountEntity) -> tuple[bool, str | None]:
    """Whether the account still has to be installed, and where if we can say.

    Two questions, and collapsing them into one nullable URL answered neither:
    `github_install_url` returns None when the deployment has not configured
    `CONNECTOR_GITHUB_APP_SLUG`, so an account that needed an installation and
    a deployment that could not name one were indistinguishable -- and both fell
    through to the page saying the connection was finished, which is the exact
    thing this page exists not to say.

    Only GitHub has this shape today, and the check is deliberately asked of the
    connectors module rather than written here as `if connector.id == "github"`:
    the controller renders a result, it does not know what any provider needs.
    """
    from app.modules.connectors.contracts.github import (
        github_install_url,
        installation_still_needed,
    )

    if not installation_still_needed(account.connector_id, account.external_ref):
        return False, None
    return True, github_install_url()


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
    needs_install, install_url = _outstanding_install(account)
    if needs_install:
        explanation = (
            "Authorizing signed you in, but it does not grant access to any "
            "repository. Install it on the account or organization whose "
            "repositories it should see, then connect again from Lemma so "
            "the installation is recorded"
        )
        return render_callback_page(
            succeeded=True,
            app_label=app_label,
            icon=connector.icon,
            title=f"{app_label} needs one more step",
            body_html=identity_html(account.display_name, account.email)
            + (
                next_step_html(
                    f"{explanation}:",
                    href=install_url,
                    label=f"Install {app_label}",
                )
                if install_url is not None
                # No slug configured, so there is no link to offer. The step is
                # still outstanding and saying so without one beats reporting a
                # connection that can read nothing as finished.
                else message_html(f"{explanation}.")
            ),
        )
    return render_callback_page(
        succeeded=True,
        app_label=app_label,
        icon=connector.icon,
        title=f"{app_label} is connected",
        body_html=identity_html(account.display_name, account.email),
    )
