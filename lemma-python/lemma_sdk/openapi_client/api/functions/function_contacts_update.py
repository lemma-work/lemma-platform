from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.function_contact_access_request import FunctionContactAccessRequest
from ...models.function_response import FunctionResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    function_name: str,
    *,
    body: FunctionContactAccessRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/pods/{pod_id}/functions/{function_name}/contacts".format(
            pod_id=quote(str(pod_id), safe=""),
            function_name=quote(str(function_name), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | FunctionResponse | None:
    if response.status_code == 200:
        response_200 = FunctionResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | FunctionResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    function_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: FunctionContactAccessRequest,
) -> Response[ErrorResponse | FunctionResponse]:
    """Open a Function to Contacts

     Let a contact's conversation call this function, or stop it. A contact holds no grant and the run
    acts for no member: it runs as the function itself, held to its own grants, and is told the asking
    contact as `contact_id`, which its input schema must declare. Takes pod settings permission and
    either owning the function or administering the pod.

    Args:
        pod_id (UUID):
        function_name (str):
        body (FunctionContactAccessRequest): Open a function to contacts, or close it.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | FunctionResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        function_name=function_name,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    function_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: FunctionContactAccessRequest,
) -> ErrorResponse | FunctionResponse | None:
    """Open a Function to Contacts

     Let a contact's conversation call this function, or stop it. A contact holds no grant and the run
    acts for no member: it runs as the function itself, held to its own grants, and is told the asking
    contact as `contact_id`, which its input schema must declare. Takes pod settings permission and
    either owning the function or administering the pod.

    Args:
        pod_id (UUID):
        function_name (str):
        body (FunctionContactAccessRequest): Open a function to contacts, or close it.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | FunctionResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        function_name=function_name,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    function_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: FunctionContactAccessRequest,
) -> Response[ErrorResponse | FunctionResponse]:
    """Open a Function to Contacts

     Let a contact's conversation call this function, or stop it. A contact holds no grant and the run
    acts for no member: it runs as the function itself, held to its own grants, and is told the asking
    contact as `contact_id`, which its input schema must declare. Takes pod settings permission and
    either owning the function or administering the pod.

    Args:
        pod_id (UUID):
        function_name (str):
        body (FunctionContactAccessRequest): Open a function to contacts, or close it.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | FunctionResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        function_name=function_name,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    function_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: FunctionContactAccessRequest,
) -> ErrorResponse | FunctionResponse | None:
    """Open a Function to Contacts

     Let a contact's conversation call this function, or stop it. A contact holds no grant and the run
    acts for no member: it runs as the function itself, held to its own grants, and is told the asking
    contact as `contact_id`, which its input schema must declare. Takes pod settings permission and
    either owning the function or administering the pod.

    Args:
        pod_id (UUID):
        function_name (str):
        body (FunctionContactAccessRequest): Open a function to contacts, or close it.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | FunctionResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            function_name=function_name,
            client=client,
            body=body,
        )
    ).parsed
