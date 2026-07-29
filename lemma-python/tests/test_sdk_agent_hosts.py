from __future__ import annotations

from typing import Any
from uuid import UUID

from lemma_sdk import Lemma


class StubTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.generated = object()

    def call(self, endpoint, *path_args, body=None, body_model=None, **kwargs):
        self.calls.append(
            {
                "endpoint": endpoint.__name__,
                "path_args": path_args,
                "body": body,
                "body_model": getattr(body_model, "__name__", None),
                "kwargs": kwargs,
            }
        )
        return {"ok": True}

    def close(self) -> None:
        pass


def _client() -> tuple[Lemma, StubTransport]:
    lemma = Lemma(token="token", base_url="https://api.example.test")
    transport = StubTransport()
    lemma._transport = transport
    return lemma, transport


def test_agent_host_management_is_typed_and_bound_to_the_user() -> None:
    lemma, transport = _client()
    host_id = "11111111-1111-4111-8111-111111111111"

    lemma.agent_hosts.list()
    lemma.agent_hosts.create_pairing(
        {
            "display_name": "My computer",
            "organization_id": "22222222-2222-4222-8222-222222222222",
        }
    )
    lemma.agent_hosts.harnesses(host_id)
    lemma.agent_hosts.revoke(host_id)

    assert [call["endpoint"].rsplit(".", 1)[-1] for call in transport.calls] == [
        "agent_host_list",
        "agent_host_pairing_create",
        "agent_host_harnesses_list",
        "agent_host_revoke",
    ]
    assert transport.calls[1]["body_model"] == "AgentHostPairingCreate"
    assert transport.calls[2]["path_args"] == (UUID(host_id),)
    assert transport.calls[3]["path_args"] == (UUID(host_id),)


def test_org_runtime_accepts_harness_profile_payload() -> None:
    lemma, transport = _client()
    lemma.org_id = "22222222-2222-4222-8222-222222222222"

    lemma.org_runtime.create_profile(
        {
            "runtime_type": "HARNESS",
            "harness_id": "33333333-3333-4333-8333-333333333333",
            "harness_snapshot_revision": "revision-1",
            "name": "Local Codex",
            "scope": "PERSONAL",
            "config_selections": {"mode": "safe"},
        }
    )

    assert transport.calls[0]["body_model"] is None
    assert (
        transport.calls[0]["body"].__class__.__name__
        == "CreateHarnessRuntimeProfileRequest"
    )


def test_org_runtime_accepts_all_declared_cloud_provider_types() -> None:
    lemma, transport = _client()
    lemma.org_id = "22222222-2222-4222-8222-222222222222"

    lemma.org_runtime.create_profile(
        {
            "runtime_type": "AZURE_OPENAI",
            "name": "Azure",
            "azure_endpoint": "https://example.openai.azure.com/openai/v1",
            "api_key": "secret",
            "default_model_name": "deployment-a",
            "model_names": ["deployment-a"],
        }
    )
    lemma.org_runtime.create_profile(
        {
            "runtime_type": "GOOGLE_VERTEX",
            "name": "Vertex",
            "project_id": "lemma-project",
            "location": "us-central1",
            "default_model_name": "gemini-2.5-pro",
            "model_names": ["gemini-2.5-pro"],
        }
    )

    assert [
        call["body"].__class__.__name__ for call in transport.calls
    ] == [
        "CreateAzureOpenAIRuntimeProfileRequest",
        "CreateGoogleVertexRuntimeProfileRequest",
    ]
