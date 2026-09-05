from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.function_revision_promote_response import FunctionRevisionPromoteResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    function_name: str,
    revision_ref: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/functions/{function_name}/revisions/{revision_ref}/promote".format(
            pod_id=quote(str(pod_id), safe=""),
            function_name=quote(str(function_name), safe=""),
            revision_ref=quote(str(revision_ref), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | FunctionRevisionPromoteResponse | None:
    if response.status_code == 200:
        response_200 = FunctionRevisionPromoteResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | FunctionRevisionPromoteResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    function_name: str,
    revision_ref: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | FunctionRevisionPromoteResponse]:
    """Promote Function Revision

     Make an existing revision the live one. Its input/output/config schemas are restored with it, since
    they are the contract its code implements.

    Args:
        pod_id (UUID):
        function_name (str):
        revision_ref (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | FunctionRevisionPromoteResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        function_name=function_name,
        revision_ref=revision_ref,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    function_name: str,
    revision_ref: str,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | FunctionRevisionPromoteResponse | None:
    """Promote Function Revision

     Make an existing revision the live one. Its input/output/config schemas are restored with it, since
    they are the contract its code implements.

    Args:
        pod_id (UUID):
        function_name (str):
        revision_ref (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | FunctionRevisionPromoteResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        function_name=function_name,
        revision_ref=revision_ref,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    function_name: str,
    revision_ref: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | FunctionRevisionPromoteResponse]:
    """Promote Function Revision

     Make an existing revision the live one. Its input/output/config schemas are restored with it, since
    they are the contract its code implements.

    Args:
        pod_id (UUID):
        function_name (str):
        revision_ref (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | FunctionRevisionPromoteResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        function_name=function_name,
        revision_ref=revision_ref,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    function_name: str,
    revision_ref: str,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | FunctionRevisionPromoteResponse | None:
    """Promote Function Revision

     Make an existing revision the live one. Its input/output/config schemas are restored with it, since
    they are the contract its code implements.

    Args:
        pod_id (UUID):
        function_name (str):
        revision_ref (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | FunctionRevisionPromoteResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            function_name=function_name,
            revision_ref=revision_ref,
            client=client,
        )
    ).parsed
