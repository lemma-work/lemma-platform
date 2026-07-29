from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.anthropic_compatible_runtime_profile_response import (
    AnthropicCompatibleRuntimeProfileResponse,
)
from ...models.azure_open_ai_runtime_profile_response import (
    AzureOpenAIRuntimeProfileResponse,
)
from ...models.error_response import ErrorResponse
from ...models.google_vertex_runtime_profile_response import (
    GoogleVertexRuntimeProfileResponse,
)
from ...models.harness_runtime_profile_response import HarnessRuntimeProfileResponse
from ...models.open_ai_compatible_runtime_profile_response import (
    OpenAICompatibleRuntimeProfileResponse,
)
from ...types import Response


def _get_kwargs(
    org_id: UUID,
    profile_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/organizations/{org_id}/runtime/profiles/{profile_id}".format(
            org_id=quote(str(org_id), safe=""),
            profile_id=quote(str(profile_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> (
    AnthropicCompatibleRuntimeProfileResponse
    | AzureOpenAIRuntimeProfileResponse
    | GoogleVertexRuntimeProfileResponse
    | HarnessRuntimeProfileResponse
    | OpenAICompatibleRuntimeProfileResponse
    | ErrorResponse
    | None
):
    if response.status_code == 200:

        def _parse_response_200(
            data: object,
        ) -> (
            AnthropicCompatibleRuntimeProfileResponse
            | AzureOpenAIRuntimeProfileResponse
            | GoogleVertexRuntimeProfileResponse
            | HarnessRuntimeProfileResponse
            | OpenAICompatibleRuntimeProfileResponse
        ):
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                response_200_type_0 = OpenAICompatibleRuntimeProfileResponse.from_dict(
                    data
                )

                return response_200_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                response_200_type_1 = (
                    AnthropicCompatibleRuntimeProfileResponse.from_dict(data)
                )

                return response_200_type_1
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                response_200_type_2 = AzureOpenAIRuntimeProfileResponse.from_dict(data)

                return response_200_type_2
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                response_200_type_3 = GoogleVertexRuntimeProfileResponse.from_dict(data)

                return response_200_type_3
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            if not isinstance(data, dict):
                raise TypeError()
            response_200_type_4 = HarnessRuntimeProfileResponse.from_dict(data)

            return response_200_type_4

        response_200 = _parse_response_200(response.json())

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
) -> Response[
    AnthropicCompatibleRuntimeProfileResponse
    | AzureOpenAIRuntimeProfileResponse
    | GoogleVertexRuntimeProfileResponse
    | HarnessRuntimeProfileResponse
    | OpenAICompatibleRuntimeProfileResponse
    | ErrorResponse
]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    org_id: UUID,
    profile_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[
    AnthropicCompatibleRuntimeProfileResponse
    | AzureOpenAIRuntimeProfileResponse
    | GoogleVertexRuntimeProfileResponse
    | HarnessRuntimeProfileResponse
    | OpenAICompatibleRuntimeProfileResponse
    | ErrorResponse
]:
    """Get a runtime profile

    Args:
        org_id (UUID):
        profile_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AnthropicCompatibleRuntimeProfileResponse | AzureOpenAIRuntimeProfileResponse | GoogleVertexRuntimeProfileResponse | HarnessRuntimeProfileResponse | OpenAICompatibleRuntimeProfileResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        org_id=org_id,
        profile_id=profile_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    org_id: UUID,
    profile_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> (
    AnthropicCompatibleRuntimeProfileResponse
    | AzureOpenAIRuntimeProfileResponse
    | GoogleVertexRuntimeProfileResponse
    | HarnessRuntimeProfileResponse
    | OpenAICompatibleRuntimeProfileResponse
    | ErrorResponse
    | None
):
    """Get a runtime profile

    Args:
        org_id (UUID):
        profile_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AnthropicCompatibleRuntimeProfileResponse | AzureOpenAIRuntimeProfileResponse | GoogleVertexRuntimeProfileResponse | HarnessRuntimeProfileResponse | OpenAICompatibleRuntimeProfileResponse | ErrorResponse
    """

    return sync_detailed(
        org_id=org_id,
        profile_id=profile_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    org_id: UUID,
    profile_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[
    AnthropicCompatibleRuntimeProfileResponse
    | AzureOpenAIRuntimeProfileResponse
    | GoogleVertexRuntimeProfileResponse
    | HarnessRuntimeProfileResponse
    | OpenAICompatibleRuntimeProfileResponse
    | ErrorResponse
]:
    """Get a runtime profile

    Args:
        org_id (UUID):
        profile_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AnthropicCompatibleRuntimeProfileResponse | AzureOpenAIRuntimeProfileResponse | GoogleVertexRuntimeProfileResponse | HarnessRuntimeProfileResponse | OpenAICompatibleRuntimeProfileResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        org_id=org_id,
        profile_id=profile_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    org_id: UUID,
    profile_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> (
    AnthropicCompatibleRuntimeProfileResponse
    | AzureOpenAIRuntimeProfileResponse
    | GoogleVertexRuntimeProfileResponse
    | HarnessRuntimeProfileResponse
    | OpenAICompatibleRuntimeProfileResponse
    | ErrorResponse
    | None
):
    """Get a runtime profile

    Args:
        org_id (UUID):
        profile_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AnthropicCompatibleRuntimeProfileResponse | AzureOpenAIRuntimeProfileResponse | GoogleVertexRuntimeProfileResponse | HarnessRuntimeProfileResponse | OpenAICompatibleRuntimeProfileResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            org_id=org_id,
            profile_id=profile_id,
            client=client,
        )
    ).parsed
