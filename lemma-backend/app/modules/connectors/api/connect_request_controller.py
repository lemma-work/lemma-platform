from __future__ import annotations

from typing import Optional
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.core.api.callback_page import (
    safe_provider_error,
    sentence,
)
from app.core.api.dependencies import CurrentUser
from app.core.config import settings
from app.modules.connectors.contracts.github import github_install_url
from app.modules.connectors.api.dependencies import ConnectorServiceDep
from app.modules.connectors.api.schemas import (
    AccountResponseSchema,
    ConnectRequestInitiateSchema,
    ConnectRequestResponseSchema,
    InstallRequestInitiateSchema,
    InstallRequestResponseSchema,
)
from app.modules.connectors.domain.account import AccountEntity
from app.modules.connectors.domain.errors import ConnectorDomainError
from app.modules.connectors.services.connect_request_lifecycle import (
    followup_account_id,
    return_path,
    safe_return_path,
)
from app.modules.connectors.services.followup_request import (
    initiate_followup_request,
    peek_connect_request,
)
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
        return_to=data.return_to,
    )

    return ConnectRequestResponseSchema.model_validate(connect_request)


#: What the callback tells the app happened, as one query parameter on the way
#: back. A connection that cannot reach anything is *not* reported as connected:
#: installation is the unit of access in the GitHub App model, so an account
#: with no installation has no repository access at all.
CONNECTED = "connected"
INSTALL_REQUIRED = "install_required"
PENDING_APPROVAL = "pending_approval"
#: An installation happened, but not through a link we started -- someone
#: installed the App from GitHub's own page. There is no connect request to
#: claim and so nothing to exchange, but there is also nothing wrong: the next
#: read of the account resolves the installation from the token it already has.
INSTALL_RECEIVED = "install_received"
ERROR = "error"

#: GitHub's word for what somebody just did on the install screen. An
#: organization member without installation rights gets this and no
#: `installation_id`, because nothing was installed -- an owner has to approve
#: first, and "connect again from Lemma" is advice that cannot succeed.
_SETUP_ACTION_REQUEST = "request"

#: Long enough to say what went wrong, short enough not to build a URL nobody
#: can log or click.
_MAX_REASON = 300


def _return_to_app(
    outcome: str,
    *,
    connector_id: str | None = None,
    account_id: object | None = None,
    reason: str | None = None,
    code: str | None = None,
    came_from: str | None = None,
) -> RedirectResponse:
    """Land the person back in Lemma rather than on a page about Lemma.

    The callback used to end on a server-rendered card in whatever tab the
    provider was opened in, which is a dead end by construction: the install
    step, the organization picker and the waiting-on-an-owner state are all
    things somebody has to *act* on, and none of them can be acted on there.
    Everything the app needs to render is in the query string; the account
    itself is fetched with the caller's own session, not passed through here.

    303 rather than 302: the callback is a GET whose effect has already
    happened, and See Other is what says the result is somewhere else.
    """
    query: dict[str, str] = {"connect": outcome}
    if connector_id:
        query["connector"] = connector_id
    if account_id is not None:
        query["account"] = str(account_id)
    if code:
        query["code"] = code
    if reason:
        query["reason"] = reason[:_MAX_REASON]
    # Deliberately the app root, not `/connectors`: that path is a stub whose
    # only job is `redirect('/')`, so landing there dropped the whole query
    # string and the person arrived home with nothing said. The connectors UI
    # itself lives under a pod, which a connect request -- scoped to an
    # organisation -- cannot name.
    # Back where the person started, when the flow recorded it. The fallback is
    # the app root rather than `/connectors`, which is a stub whose only job is
    # `redirect('/')` -- landing there dropped the whole query string and the
    # person arrived home with nothing said. `safe_return_path` is applied again
    # here rather than trusted from storage: a value that reaches a `Location`
    # header is checked at the point it becomes one.
    base = settings.frontend_url.rstrip("/")
    path = safe_return_path(came_from) or "/"
    separator = "&" if "?" in path else "?"
    return RedirectResponse(
        f"{base}{path}{separator}{urlencode(query)}", status_code=303
    )


def _is_install_return(request: Request) -> bool:
    """Whether this callback is a provider announcing an installation.

    Both parameters are GitHub's, and only an install redirect carries them.
    """
    query = request.query_params
    return bool(query.get("installation_id") or query.get("setup_action"))


def _connect_outcome(account: AccountEntity, setup_action: str | None) -> str:
    """What the person still has to do, if anything.

    Deliberately asks the connectors module what an account needs rather than
    testing `connector_id == "github"` here: the controller renders a result, it
    does not know what any provider requires.

    Only three answers are decidable here. Telling `INSTALL_REQUIRED` apart from
    "you have several installations, pick one" needs a call to GitHub with this
    account's token, which the connectors page makes when it loads -- so this
    reports the outstanding step and lets the page refine it.
    """
    from app.modules.connectors.contracts.github import installation_still_needed

    if not installation_still_needed(account.connector_id, account.external_ref):
        return CONNECTED
    if (setup_action or "").strip().lower() == _SETUP_ACTION_REQUEST:
        return PENDING_APPROVAL
    return INSTALL_REQUIRED


def _wants_json(request: Request, response_format: str | None) -> bool:
    if response_format and response_format.lower() == "json":
        return True

    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


@org_router.post(
    "/install",
    response_model=InstallRequestResponseSchema,
    operation_id="connector.connect_request.install",
    summary="Start Install Step",
    description=(
        "Start the installation leg for an account that is authorized but not "
        "yet installed, returning the URL to send the person to."
    ),
)
async def initiate_install_request(
    user: CurrentUser,
    organization_id: UUID,
    data: InstallRequestInitiateSchema,
    connector_service: ConnectorServiceDep,
) -> InstallRequestResponseSchema:
    """Where to send somebody who authorized but installed nothing.

    A separate call rather than a URL handed out with the callback result,
    because the link is only good for thirty minutes and only once: minting one
    per page load would leave a trail of unspent requests, and a stale one in a
    bookmarked URL fails in the least explicable way there is.
    """
    account = await connector_service.get_account(
        data.account_id, user.id, organization_id
    )
    url = await initiate_followup_request(
        connector_service,
        account,
        link=github_install_url,
        return_to=data.return_to,
    )
    if url is None:
        raise ConnectorDomainError(
            "This deployment has no GitHub App configured, so there is nowhere "
            "to install it from."
        )
    return InstallRequestResponseSchema(authorization_url=url)


@router.get(
    "/oauth/callback",
    operation_id="connector.oauth.callback",
    summary="OAuth Callback",
    description=(
        "Handle OAuth callback and complete account connection. This endpoint "
        "is public and uses the state parameter for security. It redirects "
        "back into the app unless JSON is explicitly requested."
    ),
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
        return _return_to_app(
            ERROR,
            code="OAUTH_PROVIDER_ERROR",
            reason=f"The provider ended the authorization with \u201c{safe_error}\u201d.",
        )

    redirect_uri = str(request.url)
    state = request.query_params.get("state")
    setup_action = request.query_params.get("setup_action")
    logger.debug("connectors.connect_request_controller.state.observed")

    # Read before the exchange claims and spends it. Two things the redirect
    # afterwards needs are recorded on the request and gone once it is claimed:
    # whether this leg *is* an install return, and where the person started.
    was_followup = False
    came_from = None
    if state:
        pending = await peek_connect_request(connector_service, state)
        if pending is not None:
            was_followup = followup_account_id(pending) is not None
            came_from = return_path(pending)

    if not state and not wants_json and _is_install_return(request):
        # An install started from GitHub's own app page rather than from a link
        # Lemma minted, so there is no `state` and nothing to claim. Exchanging
        # the code anyway would mean completing a connection nobody here began,
        # which is the shape of the substitution attack the identity binding
        # exists to refuse. Reconciliation reaches the same end state safely:
        # the account's own token can be asked which installations it can see.
        logger.info("connectors.connect_request_controller.install_return.diagnostic")
        return _return_to_app(INSTALL_RECEIVED)

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
        return _return_to_app(
            ERROR, code=exc.code, reason=sentence(exc.message), came_from=came_from
        )

    account_response = AccountResponseSchema.model_validate(account)
    account_response.kind = await connector_service.get_account_kind(account)
    if wants_json:
        return JSONResponse(content=account_response.model_dump(mode="json"))

    outcome = _connect_outcome(account, setup_action)
    if outcome == INSTALL_REQUIRED and not was_followup:
        # Straight on to the install rather than back through Lemma to be told
        # to go there. Authorizing and installing are two things GitHub needs
        # and one thing the person asked for, and a stop in the middle to read
        # a card about it is the part that feels broken.
        #
        # Only when this callback is not itself the return from an install:
        # somebody who declines on GitHub's install screen would otherwise be
        # sent straight back to it, forever.
        install_url = await initiate_followup_request(
            connector_service,
            account,
            link=github_install_url,
            return_to=came_from,
        )
        if install_url is not None:
            logger.info(
                "connectors.connect_request_controller.install_chained.diagnostic"
            )
            return RedirectResponse(install_url, status_code=303)

    return _return_to_app(
        outcome,
        connector_id=account.connector_id,
        account_id=account.id,
        came_from=came_from,
    )
