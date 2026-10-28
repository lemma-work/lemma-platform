"""A pod's connectors and the accounts in it, over HTTP.

Everything an operation runs through is under ``/pods/{pod_id}``: the pod
context pins a workload's token to its own pod there, so an agent or function
reaches its pod's connectors and nothing else, and no route outside a pod has
to be opened to workloads for connectors to work. The org-level execute route
these replace is gone.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Request, status

from app.core.api.actor_policy import require_person
from app.core.api.dependencies import CurrentUser
from app.core.authorization.context import ResourceRef
from app.core.authorization.dependencies import PodContextDep
from app.core.authorization.permissions import Permissions
from app.modules.connectors.api.dependencies import (
    ConnectorOperationServiceDep,
    ConnectorServiceDep,
    ConnectorOperationUseCasesDep,
    PodConnectorCoverageServiceDep,
    PodConnectorServiceDep,
)
from app.modules.connectors.api.schemas.connector_operation_schemas import (
    OperationDetail,
    OperationDiscoverResponse,
    OperationExecutionRequest,
    OperationExecutionResponse,
)
from app.modules.connectors.services.install_on_connect import install_to_connect
from app.modules.connectors.api.schemas.pod_connector_schemas import (
    AccountPodUsageResponse,
    AskMembersRequest,
    AskMembersResponse,
    InstallationImpactResponse,
    PodAccountCreateRequest,
    PodAccountListResponse,
    PodAccountResponse,
    PodAccountShareAsRequest,
    PodAccountUpdateRequest,
    PodConnectorCoverage,
    PodConnectorCreateRequest,
    PodConnectorListResponse,
    PodConnectorResponse,
    PodConnectorUpdateRequest,
)

router = APIRouter(prefix="/pods/{pod_id}", tags=["Connectors"])
me_router = APIRouter(prefix="/me/connectors", tags=["Connectors"])
installation_router = APIRouter(
    prefix="/organizations/{organization_id}/installations", tags=["Connectors"]
)

#: Sharing a credential, or putting one in the pod, is a person's own act.
_PERSON_ONLY = require_person("share or add a connected account")


@router.get(
    "/connectors",
    response_model=PodConnectorListResponse,
    operation_id="pod.connector.list",
    summary="List Pod Connectors",
)
async def list_pod_connectors(
    pod_id: UUID, ctx: PodContextDep, service: PodConnectorServiceDep
) -> PodConnectorListResponse:
    return PodConnectorListResponse(items=await service.list_connectors(ctx, pod_id))


@router.post(
    "/connectors",
    response_model=PodConnectorResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="pod.connector.create",
    summary="Declare A Pod Connector",
    description=(
        "Declare a connector by an organization installation "
        "(`auth_config_id`) or by a catalog connector (`connector_id`): its "
        "organization default, or Lemma's own app for it installed now when the "
        "organization has none and the caller may install connectors. "
        "`slug` defaults to the installation's name; `mode` to PER_MEMBER."
    ),
)
async def create_pod_connector(
    pod_id: UUID,
    body: PodConnectorCreateRequest,
    ctx: PodContextDep,
    user: CurrentUser,
    service: PodConnectorServiceDep,
    connector_service: ConnectorServiceDep,
) -> PodConnectorResponse:
    if body.connector_id is not None and ctx.organization_id is not None:
        # Asked first: installing for the organization is a step further than
        # declaring a connector, and nobody who may not declare one gets it.
        await ctx.require(Permissions.POD_CONNECTOR_MANAGE, ResourceRef.pod(pod_id))
        install = await install_to_connect(
            connector_service,
            user_id=user.id,
            organization_id=ctx.organization_id,
            connector_id=body.connector_id,
            auth_config_id=None,
        )
        body = body.model_copy(update={"auth_config_id": install.id})
    return await service.create_connector(ctx, pod_id, body)


@router.get(
    "/connectors/{slug}",
    response_model=PodConnectorResponse,
    operation_id="pod.connector.get",
    summary="Get Pod Connector",
)
async def get_pod_connector(
    pod_id: UUID, slug: str, ctx: PodContextDep, service: PodConnectorServiceDep
) -> PodConnectorResponse:
    return await service.get_connector(ctx, pod_id, slug)


@router.patch(
    "/connectors/{slug}",
    response_model=PodConnectorResponse,
    operation_id="pod.connector.update",
    summary="Update Pod Connector",
)
async def update_pod_connector(
    pod_id: UUID,
    slug: str,
    body: PodConnectorUpdateRequest,
    ctx: PodContextDep,
    service: PodConnectorServiceDep,
) -> PodConnectorResponse:
    return await service.update_connector(ctx, pod_id, slug, body)


@router.delete(
    "/connectors/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="pod.connector.delete",
    summary="Remove Pod Connector",
)
async def delete_pod_connector(
    pod_id: UUID, slug: str, ctx: PodContextDep, service: PodConnectorServiceDep
) -> None:
    await service.delete_connector(ctx, pod_id, slug)


@router.get(
    "/connectors/{slug}/coverage",
    response_model=PodConnectorCoverage,
    operation_id="pod.connector.coverage",
    summary="Who A Per-Member Connector Is Waiting On",
    description=(
        "For someone who may manage the pod's connectors: of the members who "
        "may use this PER_MEMBER connector, how many have connected a working "
        "account, and who has not. Viewers, and anyone a RESTRICTED connector "
        "leaves out, are not counted. Asked for one connector at a time, "
        "because it weighs each member's own access; listings leave it out."
    ),
)
async def pod_connector_coverage(
    pod_id: UUID,
    slug: str,
    ctx: PodContextDep,
    coverage: PodConnectorCoverageServiceDep,
) -> PodConnectorCoverage:
    return await coverage.coverage(ctx, pod_id, slug)


@router.post(
    "/connectors/{slug}/ask-members",
    response_model=AskMembersResponse,
    operation_id="pod.connector.ask_members",
    summary="Ask Members To Connect",
    description=(
        'Post one line, "This pod uses <connector> — connect yours: '
        '<connect_url>", in the own channel of each member a PER_MEMBER '
        "connector is waiting on (as `coverage` counts them). A member is asked "
        "about one connector at most once a day; those asked already are "
        "returned as `asked_recently`, and those whose line could not be "
        "posted, or whose last ask could not be checked, as `not_asked`."
    ),
    dependencies=[require_person("ask members to connect")],
)
async def ask_members_to_connect(
    pod_id: UUID,
    slug: str,
    ctx: PodContextDep,
    coverage: PodConnectorCoverageServiceDep,
    body: AskMembersRequest | None = None,
) -> AskMembersResponse:
    return await coverage.ask_members(
        ctx, pod_id, slug, user_ids=body.user_ids if body else None
    )


@router.get(
    "/connectors/{slug}/operations",
    response_model=OperationDiscoverResponse,
    operation_id="pod.connector.operation.list",
    summary="Discover Pod Connector Operations",
)
async def list_pod_connector_operations(
    pod_id: UUID,
    slug: str,
    ctx: PodContextDep,
    service: ConnectorOperationServiceDep,
    query: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> OperationDiscoverResponse:
    return await service.pod_connector_operations(
        ctx, pod_id=pod_id, slug=slug, query=query, limit=limit
    )


@router.get(
    "/connectors/{slug}/operations/{operation_name}",
    response_model=OperationDetail,
    operation_id="pod.connector.operation.get",
    summary="Get Pod Connector Operation",
)
async def get_pod_connector_operation(
    pod_id: UUID,
    slug: str,
    operation_name: str,
    ctx: PodContextDep,
    service: ConnectorOperationServiceDep,
) -> OperationDetail:
    return await service.pod_connector_operation(
        ctx, pod_id=pod_id, slug=slug, operation_name=operation_name
    )


@router.post(
    "/connectors/{slug}/operations/{operation_name}/execute",
    response_model=OperationExecutionResponse,
    operation_id="pod.connector.operation.execute",
    summary="Execute Pod Connector Operation",
    description=(
        "Run an operation through a pod connector. The account it runs as is "
        "decided by the connector: the caller's own for a PER_MEMBER connector, "
        "the shared one for a SHARED connector. Typed refusals: "
        "`CONNECTION_REQUIRED` and `REAUTH_REQUIRED` (with `connect_url`), "
        "`CONNECTOR_NEEDS_ACCOUNT`, `CONNECTOR_UNAVAILABLE`."
    ),
)
async def execute_pod_connector_operation(
    pod_id: UUID,
    slug: str,
    operation_name: str,
    body: OperationExecutionRequest,
    request: Request,
    user: CurrentUser,
    use_cases: ConnectorOperationUseCasesDep,
) -> OperationExecutionResponse:
    # No request-scoped context: the use case authorizes and resolves in a
    # short scope of its own and makes the provider call holding no pooled
    # connection.
    return await use_cases.execute_pod_connector_operation(
        pod_id=pod_id,
        slug=slug,
        operation_name=operation_name,
        payload=body.payload,
        user_id=user.id,
        request=request,
    )


@router.get(
    "/connector-accounts",
    response_model=PodAccountListResponse,
    operation_id="pod.connector_account.list",
    summary="List Accounts In Use In The Pod",
)
async def list_pod_accounts(
    pod_id: UUID, ctx: PodContextDep, service: PodConnectorServiceDep
) -> PodAccountListResponse:
    return PodAccountListResponse(items=await service.list_accounts(ctx, pod_id))


@router.post(
    "/connector-accounts",
    response_model=PodAccountResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="pod.connector_account.add",
    summary="Add One Of Your Accounts To The Pod",
    dependencies=[_PERSON_ONLY],
)
async def add_pod_account(
    pod_id: UUID,
    body: PodAccountCreateRequest,
    ctx: PodContextDep,
    service: PodConnectorServiceDep,
) -> PodAccountResponse:
    return await service.add_account(
        ctx,
        pod_id,
        account_id=body.account_id,
        visibility=body.visibility,
        share_with=body.share_with,
    )


@router.patch(
    "/connector-accounts/{pod_account_id}",
    response_model=PodAccountResponse,
    operation_id="pod.connector_account.update",
    summary="Share Or Withdraw Your Account In The Pod",
    dependencies=[_PERSON_ONLY],
)
async def update_pod_account(
    pod_id: UUID,
    pod_account_id: UUID,
    body: PodAccountUpdateRequest,
    ctx: PodContextDep,
    service: PodConnectorServiceDep,
) -> PodAccountResponse:
    return await service.update_account(
        ctx,
        pod_id,
        pod_account_id,
        visibility=body.visibility,
        share_with=body.share_with,
        is_member_default=body.is_member_default,
        allow_sandbox_use=body.allow_sandbox_use,
    )


@router.post(
    "/connector-accounts/{pod_account_id}/share-as",
    response_model=PodConnectorResponse,
    operation_id="pod.connector_account.share_as",
    summary="Share Your Account As A Pod Connector",
    description=(
        "Owner only, and the caller must also manage the pod's connectors: "
        "share this pod account with the pod (if it is still yours alone) and "
        "run the SHARED connector `slug` as it -- pointing the existing one, "
        "or declaring it. Checked in full before anything changes, and done in "
        "one transaction: a refusal leaves the account as it was."
    ),
    dependencies=[_PERSON_ONLY],
)
async def share_pod_account_as(
    pod_id: UUID,
    pod_account_id: UUID,
    body: PodAccountShareAsRequest,
    ctx: PodContextDep,
    service: PodConnectorServiceDep,
) -> PodConnectorResponse:
    from app.modules.connectors.services.pod_account_share_as import (
        share_account_as,
    )

    return await share_account_as(
        service, ctx, pod_id, pod_account_id, slug=body.slug, purpose=body.purpose
    )


@router.delete(
    "/connector-accounts/{pod_account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="pod.connector_account.remove",
    summary="Remove An Account From The Pod",
)
async def remove_pod_account(
    pod_id: UUID,
    pod_account_id: UUID,
    ctx: PodContextDep,
    service: PodConnectorServiceDep,
) -> None:
    await service.remove_account(ctx, pod_id, pod_account_id)


@me_router.get(
    "/accounts/{account_id}/pods",
    response_model=AccountPodUsageResponse,
    operation_id="connector.account.pods",
    summary="Where One Of My Accounts Is Used",
)
async def account_pod_usage(
    account_id: UUID, user: CurrentUser, service: PodConnectorServiceDep
) -> AccountPodUsageResponse:
    return AccountPodUsageResponse(
        account_id=account_id,
        items=await service.where_used(user_id=user.id, account_id=account_id),
    )


@installation_router.get(
    "/{auth_config_id}/impact",
    response_model=InstallationImpactResponse,
    operation_id="connector.installation.impact",
    summary="What Removing An Installation Would Stop",
    description=(
        "Accounts, pod accounts and pod connectors on the installation, and the "
        "surfaces, schedules and MCP event subscriptions that run through them: "
        "organization-wide counts, and per pod for the pods the caller can open. "
        "If the second half cannot be read just now the first is still "
        "returned, with `complete: false`."
    ),
)
async def installation_impact(
    organization_id: UUID,
    auth_config_id: UUID,
    user: CurrentUser,
    service: PodConnectorServiceDep,
) -> InstallationImpactResponse:
    # Imported here: it reaches the surfaces, schedules and pods contracts,
    # which nothing else on a connector route needs.
    from app.modules.connectors.services.installation_impact import (
        with_what_else_stops,
    )

    impact = await service.impact(
        user_id=user.id,
        organization_id=organization_id,
        auth_config_id=auth_config_id,
    )
    return await with_what_else_stops(service.uow, impact, user_id=user.id)
