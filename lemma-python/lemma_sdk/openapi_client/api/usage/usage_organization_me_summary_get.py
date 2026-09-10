import datetime
from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.usage_summary_response import UsageSummaryResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    organization_id: UUID,
    *,
    agent_run_id: None | Unset | UUID = UNSET,
    conversation_id: None | Unset | UUID = UNSET,
    start: datetime.datetime | None | Unset = UNSET,
    end: datetime.datetime | None | Unset = UNSET,
    days: int | Unset = 30,
    limit: int | Unset = 100,
    pod_id: None | Unset | UUID = UNSET,
    user_id: None | Unset | UUID = UNSET,
    agent_id: None | Unset | UUID = UNSET,
    profile_id: None | str | Unset = UNSET,
    profile_scope: None | str | Unset = UNSET,
    model_name: None | str | Unset = UNSET,
    usage_kind: None | str | Unset = UNSET,
    source_type: None | str | Unset = UNSET,
    status: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_agent_run_id: None | str | Unset
    if isinstance(agent_run_id, Unset):
        json_agent_run_id = UNSET
    elif isinstance(agent_run_id, UUID):
        json_agent_run_id = str(agent_run_id)
    else:
        json_agent_run_id = agent_run_id
    params["agent_run_id"] = json_agent_run_id

    json_conversation_id: None | str | Unset
    if isinstance(conversation_id, Unset):
        json_conversation_id = UNSET
    elif isinstance(conversation_id, UUID):
        json_conversation_id = str(conversation_id)
    else:
        json_conversation_id = conversation_id
    params["conversation_id"] = json_conversation_id

    json_start: None | str | Unset
    if isinstance(start, Unset):
        json_start = UNSET
    elif isinstance(start, datetime.datetime):
        json_start = start.isoformat()
    else:
        json_start = start
    params["start"] = json_start

    json_end: None | str | Unset
    if isinstance(end, Unset):
        json_end = UNSET
    elif isinstance(end, datetime.datetime):
        json_end = end.isoformat()
    else:
        json_end = end
    params["end"] = json_end

    params["days"] = days

    params["limit"] = limit

    json_pod_id: None | str | Unset
    if isinstance(pod_id, Unset):
        json_pod_id = UNSET
    elif isinstance(pod_id, UUID):
        json_pod_id = str(pod_id)
    else:
        json_pod_id = pod_id
    params["pod_id"] = json_pod_id

    json_user_id: None | str | Unset
    if isinstance(user_id, Unset):
        json_user_id = UNSET
    elif isinstance(user_id, UUID):
        json_user_id = str(user_id)
    else:
        json_user_id = user_id
    params["user_id"] = json_user_id

    json_agent_id: None | str | Unset
    if isinstance(agent_id, Unset):
        json_agent_id = UNSET
    elif isinstance(agent_id, UUID):
        json_agent_id = str(agent_id)
    else:
        json_agent_id = agent_id
    params["agent_id"] = json_agent_id

    json_profile_id: None | str | Unset
    if isinstance(profile_id, Unset):
        json_profile_id = UNSET
    else:
        json_profile_id = profile_id
    params["profile_id"] = json_profile_id

    json_profile_scope: None | str | Unset
    if isinstance(profile_scope, Unset):
        json_profile_scope = UNSET
    else:
        json_profile_scope = profile_scope
    params["profile_scope"] = json_profile_scope

    json_model_name: None | str | Unset
    if isinstance(model_name, Unset):
        json_model_name = UNSET
    else:
        json_model_name = model_name
    params["model_name"] = json_model_name

    json_usage_kind: None | str | Unset
    if isinstance(usage_kind, Unset):
        json_usage_kind = UNSET
    else:
        json_usage_kind = usage_kind
    params["usage_kind"] = json_usage_kind

    json_source_type: None | str | Unset
    if isinstance(source_type, Unset):
        json_source_type = UNSET
    else:
        json_source_type = source_type
    params["source_type"] = json_source_type

    json_status: None | str | Unset
    if isinstance(status, Unset):
        json_status = UNSET
    else:
        json_status = status
    params["status"] = json_status

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/usage/organizations/{organization_id}/me".format(
            organization_id=quote(str(organization_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | UsageSummaryResponse | None:
    if response.status_code == 200:
        response_200 = UsageSummaryResponse.from_dict(response.json())

        return response_200

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | UsageSummaryResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    agent_run_id: None | Unset | UUID = UNSET,
    conversation_id: None | Unset | UUID = UNSET,
    start: datetime.datetime | None | Unset = UNSET,
    end: datetime.datetime | None | Unset = UNSET,
    days: int | Unset = 30,
    limit: int | Unset = 100,
    pod_id: None | Unset | UUID = UNSET,
    user_id: None | Unset | UUID = UNSET,
    agent_id: None | Unset | UUID = UNSET,
    profile_id: None | str | Unset = UNSET,
    profile_scope: None | str | Unset = UNSET,
    model_name: None | str | Unset = UNSET,
    usage_kind: None | str | Unset = UNSET,
    source_type: None | str | Unset = UNSET,
    status: None | str | Unset = UNSET,
) -> Response[ErrorResponse | UsageSummaryResponse]:
    """Get My Usage

    Args:
        organization_id (UUID):
        agent_run_id (None | Unset | UUID):
        conversation_id (None | Unset | UUID):
        start (datetime.datetime | None | Unset):
        end (datetime.datetime | None | Unset):
        days (int | Unset):  Default: 30.
        limit (int | Unset):  Default: 100.
        pod_id (None | Unset | UUID):
        user_id (None | Unset | UUID):
        agent_id (None | Unset | UUID):
        profile_id (None | str | Unset):
        profile_scope (None | str | Unset):
        model_name (None | str | Unset):
        usage_kind (None | str | Unset):
        source_type (None | str | Unset):
        status (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | UsageSummaryResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        agent_run_id=agent_run_id,
        conversation_id=conversation_id,
        start=start,
        end=end,
        days=days,
        limit=limit,
        pod_id=pod_id,
        user_id=user_id,
        agent_id=agent_id,
        profile_id=profile_id,
        profile_scope=profile_scope,
        model_name=model_name,
        usage_kind=usage_kind,
        source_type=source_type,
        status=status,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    agent_run_id: None | Unset | UUID = UNSET,
    conversation_id: None | Unset | UUID = UNSET,
    start: datetime.datetime | None | Unset = UNSET,
    end: datetime.datetime | None | Unset = UNSET,
    days: int | Unset = 30,
    limit: int | Unset = 100,
    pod_id: None | Unset | UUID = UNSET,
    user_id: None | Unset | UUID = UNSET,
    agent_id: None | Unset | UUID = UNSET,
    profile_id: None | str | Unset = UNSET,
    profile_scope: None | str | Unset = UNSET,
    model_name: None | str | Unset = UNSET,
    usage_kind: None | str | Unset = UNSET,
    source_type: None | str | Unset = UNSET,
    status: None | str | Unset = UNSET,
) -> ErrorResponse | UsageSummaryResponse | None:
    """Get My Usage

    Args:
        organization_id (UUID):
        agent_run_id (None | Unset | UUID):
        conversation_id (None | Unset | UUID):
        start (datetime.datetime | None | Unset):
        end (datetime.datetime | None | Unset):
        days (int | Unset):  Default: 30.
        limit (int | Unset):  Default: 100.
        pod_id (None | Unset | UUID):
        user_id (None | Unset | UUID):
        agent_id (None | Unset | UUID):
        profile_id (None | str | Unset):
        profile_scope (None | str | Unset):
        model_name (None | str | Unset):
        usage_kind (None | str | Unset):
        source_type (None | str | Unset):
        status (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | UsageSummaryResponse
    """

    return sync_detailed(
        organization_id=organization_id,
        client=client,
        agent_run_id=agent_run_id,
        conversation_id=conversation_id,
        start=start,
        end=end,
        days=days,
        limit=limit,
        pod_id=pod_id,
        user_id=user_id,
        agent_id=agent_id,
        profile_id=profile_id,
        profile_scope=profile_scope,
        model_name=model_name,
        usage_kind=usage_kind,
        source_type=source_type,
        status=status,
    ).parsed


async def asyncio_detailed(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    agent_run_id: None | Unset | UUID = UNSET,
    conversation_id: None | Unset | UUID = UNSET,
    start: datetime.datetime | None | Unset = UNSET,
    end: datetime.datetime | None | Unset = UNSET,
    days: int | Unset = 30,
    limit: int | Unset = 100,
    pod_id: None | Unset | UUID = UNSET,
    user_id: None | Unset | UUID = UNSET,
    agent_id: None | Unset | UUID = UNSET,
    profile_id: None | str | Unset = UNSET,
    profile_scope: None | str | Unset = UNSET,
    model_name: None | str | Unset = UNSET,
    usage_kind: None | str | Unset = UNSET,
    source_type: None | str | Unset = UNSET,
    status: None | str | Unset = UNSET,
) -> Response[ErrorResponse | UsageSummaryResponse]:
    """Get My Usage

    Args:
        organization_id (UUID):
        agent_run_id (None | Unset | UUID):
        conversation_id (None | Unset | UUID):
        start (datetime.datetime | None | Unset):
        end (datetime.datetime | None | Unset):
        days (int | Unset):  Default: 30.
        limit (int | Unset):  Default: 100.
        pod_id (None | Unset | UUID):
        user_id (None | Unset | UUID):
        agent_id (None | Unset | UUID):
        profile_id (None | str | Unset):
        profile_scope (None | str | Unset):
        model_name (None | str | Unset):
        usage_kind (None | str | Unset):
        source_type (None | str | Unset):
        status (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | UsageSummaryResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        agent_run_id=agent_run_id,
        conversation_id=conversation_id,
        start=start,
        end=end,
        days=days,
        limit=limit,
        pod_id=pod_id,
        user_id=user_id,
        agent_id=agent_id,
        profile_id=profile_id,
        profile_scope=profile_scope,
        model_name=model_name,
        usage_kind=usage_kind,
        source_type=source_type,
        status=status,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    agent_run_id: None | Unset | UUID = UNSET,
    conversation_id: None | Unset | UUID = UNSET,
    start: datetime.datetime | None | Unset = UNSET,
    end: datetime.datetime | None | Unset = UNSET,
    days: int | Unset = 30,
    limit: int | Unset = 100,
    pod_id: None | Unset | UUID = UNSET,
    user_id: None | Unset | UUID = UNSET,
    agent_id: None | Unset | UUID = UNSET,
    profile_id: None | str | Unset = UNSET,
    profile_scope: None | str | Unset = UNSET,
    model_name: None | str | Unset = UNSET,
    usage_kind: None | str | Unset = UNSET,
    source_type: None | str | Unset = UNSET,
    status: None | str | Unset = UNSET,
) -> ErrorResponse | UsageSummaryResponse | None:
    """Get My Usage

    Args:
        organization_id (UUID):
        agent_run_id (None | Unset | UUID):
        conversation_id (None | Unset | UUID):
        start (datetime.datetime | None | Unset):
        end (datetime.datetime | None | Unset):
        days (int | Unset):  Default: 30.
        limit (int | Unset):  Default: 100.
        pod_id (None | Unset | UUID):
        user_id (None | Unset | UUID):
        agent_id (None | Unset | UUID):
        profile_id (None | str | Unset):
        profile_scope (None | str | Unset):
        model_name (None | str | Unset):
        usage_kind (None | str | Unset):
        source_type (None | str | Unset):
        status (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | UsageSummaryResponse
    """

    return (
        await asyncio_detailed(
            organization_id=organization_id,
            client=client,
            agent_run_id=agent_run_id,
            conversation_id=conversation_id,
            start=start,
            end=end,
            days=days,
            limit=limit,
            pod_id=pod_id,
            user_id=user_id,
            agent_id=agent_id,
            profile_id=profile_id,
            profile_scope=profile_scope,
            model_name=model_name,
            usage_kind=usage_kind,
            source_type=source_type,
            status=status,
        )
    ).parsed
