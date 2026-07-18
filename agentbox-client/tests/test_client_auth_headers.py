"""Both agentbox clients must send the manager key via X-API-Key.

X-API-Key is a custom header that ingresses/proxies pass through untouched,
whereas Authorization is frequently stripped or rewritten in transit. The
manager key must therefore travel on X-API-Key, not only on Authorization.
"""
from __future__ import annotations

from agentbox_client import AgentBoxClient
from agentbox_client.apps.function_executor import FunctionExecutorClient


def test_agentbox_client_sends_manager_key_only_as_x_api_key() -> None:
    client = AgentBoxClient(base_url="http://agentbox", api_key="manager-key")
    headers = client.client.headers
    assert headers["x-api-key"] == "manager-key"
    # The manager key must NOT travel on Authorization — that header is reserved
    # for the function/lemma token.
    assert "authorization" not in headers


def test_function_executor_client_sends_manager_key_as_x_api_key() -> None:
    client = FunctionExecutorClient(
        manager_base_url="http://agentbox",
        manager_api_key="manager-key",
        lemma_token="user-token",
    )
    headers = client.client.headers
    assert headers["x-api-key"] == "manager-key"
    # The user's lemma token rides on Authorization, not the manager key.
    assert headers["authorization"] == "Bearer user-token"


def test_context_provider_forwards_only_lemma_correlation_headers() -> None:
    provided = {
        "x-request-id": "request-1",
        "x-lemma-correlation-id": "correlation-1",
        "x-lemma-event-id": "event-1",
        "x-lemma-job-id": "job-1",
        "authorization": "must-not-override",
        "x-provider-header": "must-not-leak",
    }
    client = AgentBoxClient(
        base_url="http://agentbox",
        api_key="manager-key",
        context_headers_provider=lambda: provided,
    )
    assert client._context_headers() == {
        key: value for key, value in provided.items() if key.startswith("x-lemma-")
    } | {"x-request-id": "request-1"}


def test_function_executor_context_provider_is_best_effort_and_filtered() -> None:
    client = FunctionExecutorClient(
        manager_base_url="http://agentbox",
        manager_api_key="manager-key",
        lemma_token="user-token",
        context_headers_provider=lambda: {
            "x-request-id": "request-2",
            "authorization": "must-not-override",
        },
    )
    assert client._context_headers() == {"x-request-id": "request-2"}

    client._context_headers_provider = lambda: (_ for _ in ()).throw(RuntimeError())
    assert client._context_headers() is None
