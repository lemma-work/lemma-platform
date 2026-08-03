from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.connector_skill_response import ConnectorSkillResponse
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    connector_id: str,
    *,
    kind: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_kind: None | str | Unset
    if isinstance(kind, Unset):
        json_kind = UNSET
    else:
        json_kind = kind
    params["kind"] = json_kind

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/connectors/{connector_id}/skill".format(
            connector_id=quote(str(connector_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ConnectorSkillResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = ConnectorSkillResponse.from_dict(response.json())

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
) -> Response[ConnectorSkillResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    connector_id: str,
    *,
    client: AuthenticatedClient | Client,
    kind: None | str | Unset = UNSET,
) -> Response[ConnectorSkillResponse | ErrorResponse]:
    """Get Connector Skill

     Get the skill guide markdown for a connector. Pass `kind=package` or `kind=composio` to get kind-
    specific instructions when the app supports both. Falls back to the generic doc if no kind-specific
    file exists. Returns 404 if no skill doc has been generated yet.

    Args:
        connector_id (str):
        kind (None | str | Unset): Kind override, e.g. package or composio

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConnectorSkillResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        connector_id=connector_id,
        kind=kind,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    connector_id: str,
    *,
    client: AuthenticatedClient | Client,
    kind: None | str | Unset = UNSET,
) -> ConnectorSkillResponse | ErrorResponse | None:
    """Get Connector Skill

     Get the skill guide markdown for a connector. Pass `kind=package` or `kind=composio` to get kind-
    specific instructions when the app supports both. Falls back to the generic doc if no kind-specific
    file exists. Returns 404 if no skill doc has been generated yet.

    Args:
        connector_id (str):
        kind (None | str | Unset): Kind override, e.g. package or composio

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConnectorSkillResponse | ErrorResponse
    """

    return sync_detailed(
        connector_id=connector_id,
        client=client,
        kind=kind,
    ).parsed


async def asyncio_detailed(
    connector_id: str,
    *,
    client: AuthenticatedClient | Client,
    kind: None | str | Unset = UNSET,
) -> Response[ConnectorSkillResponse | ErrorResponse]:
    """Get Connector Skill

     Get the skill guide markdown for a connector. Pass `kind=package` or `kind=composio` to get kind-
    specific instructions when the app supports both. Falls back to the generic doc if no kind-specific
    file exists. Returns 404 if no skill doc has been generated yet.

    Args:
        connector_id (str):
        kind (None | str | Unset): Kind override, e.g. package or composio

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConnectorSkillResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        connector_id=connector_id,
        kind=kind,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    connector_id: str,
    *,
    client: AuthenticatedClient | Client,
    kind: None | str | Unset = UNSET,
) -> ConnectorSkillResponse | ErrorResponse | None:
    """Get Connector Skill

     Get the skill guide markdown for a connector. Pass `kind=package` or `kind=composio` to get kind-
    specific instructions when the app supports both. Falls back to the generic doc if no kind-specific
    file exists. Returns 404 if no skill doc has been generated yet.

    Args:
        connector_id (str):
        kind (None | str | Unset): Kind override, e.g. package or composio

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConnectorSkillResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            connector_id=connector_id,
            client=client,
            kind=kind,
        )
    ).parsed
