from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.mcp_access.api.oauth_routes import (
    authorization_server_metadata,
    protected_resource_metadata,
)
from app.modules.mcp_access.domain.entities import Scope, parse_scopes
from app.modules.mcp_access.domain.resources import (
    pod_id_for_resource,
    pod_resource_url,
    protected_resource_metadata_url,
)
from app.modules.mcp_access.domain.tokens import digest, looks_like, mint
from app.modules.mcp_access.domain.entities import TokenKind

pytestmark = pytest.mark.unit

API = "https://api.lemma.work"


def test_a_pod_resource_round_trips_through_its_url():
    pod_id = uuid4()
    url = pod_resource_url(API, pod_id)
    assert url == f"https://api.lemma.work/mcp/{pod_id}"
    assert pod_id_for_resource(API, url) == pod_id


@pytest.mark.parametrize(
    "variant",
    ["HTTPS://API.LEMMA.WORK/mcp/{pod}", "https://api.lemma.work/mcp/{pod}/"],
)
def test_resource_comparison_ignores_what_rfc_3986_says_is_the_same(variant):
    pod_id = uuid4()
    assert pod_id_for_resource(API, variant.format(pod=pod_id)) == pod_id


@pytest.mark.parametrize(
    "resource",
    [
        None,
        "",
        "https://evil.example/mcp/{pod}",
        "https://api.lemma.work/agent-runtime/pods/{pod}/mcp",
        "https://api.lemma.work/mcp/not-a-pod",
        "https://api.lemma.work/mcp/{pod}?x=1",
        "https://api.lemma.work/mcp/{pod}/extra",
        "http://api.lemma.work/mcp/{pod}",
    ],
)
def test_anything_but_this_servers_pod_url_names_no_pod(resource):
    """A token must never be issued for a resource this server cannot name --
    `invalid_target` rather than a guess."""
    pod_id = uuid4()
    raw = resource.format(pod=pod_id) if resource else resource
    assert pod_id_for_resource(API, raw) is None


def test_metadata_document_url_inserts_the_well_known_segment_before_the_path():
    pod_id = uuid4()
    assert protected_resource_metadata_url(API, pod_id) == (
        f"https://api.lemma.work/.well-known/oauth-protected-resource/mcp/{pod_id}"
    )
    # Behind a path prefix, the well-known segment still goes at the host root.
    assert protected_resource_metadata_url("https://lemma.example/api/", pod_id) == (
        f"https://lemma.example/.well-known/oauth-protected-resource/api/mcp/{pod_id}"
    )


def test_no_scopes_named_means_none_and_unknown_scopes_grant_nothing():
    # Empty is nothing. "Everything" is decided at authorize, never inferred
    # from an empty stored list.
    assert parse_scopes(None) == frozenset()
    assert parse_scopes([]) == frozenset()
    assert parse_scopes(["pod:read", "offline_access", "admin"]) == {Scope.READ}


def test_tokens_are_prefixed_by_kind_and_stored_only_as_a_digest():
    access = mint(TokenKind.ACCESS)
    refresh = mint(TokenKind.REFRESH)
    assert looks_like(access, TokenKind.ACCESS)
    assert not looks_like(access, TokenKind.REFRESH)
    assert looks_like(refresh, TokenKind.REFRESH)
    assert access != mint(TokenKind.ACCESS)
    assert len(digest(access)) == 64 and access not in digest(access)


def test_authorization_server_metadata_says_what_claude_and_chatgpt_look_for():
    metadata = authorization_server_metadata(API)
    assert metadata["issuer"] == API
    # Spec-following clients refuse a server without this.
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    # Claude uses a client metadata document only when both are present.
    assert metadata["client_id_metadata_document_supported"] is True
    assert "none" in metadata["token_endpoint_auth_methods_supported"]
    assert metadata["authorization_response_iss_parameter_supported"] is True
    assert metadata["registration_endpoint"] == f"{API}/oauth/register"
    assert "offline_access" not in metadata["scopes_supported"]


def test_protected_resource_metadata_names_the_pod_url_and_this_server():
    pod_id = uuid4()
    metadata = protected_resource_metadata(API, pod_id)
    assert metadata["resource"] == pod_resource_url(API, pod_id)
    assert metadata["authorization_servers"] == [API]
    assert str(pod_id) not in str(metadata.get("resource_name"))


def _form_request(body: bytes, headers: list[tuple[bytes, bytes]] | None = None):
    from starlette.requests import Request

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/oauth/token",
            "client": ("203.0.113.9", 1),
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")]
            + (headers or []),
        },
        receive,
    )


@pytest.mark.asyncio
async def test_the_token_limit_is_per_client_as_well_as_per_address():
    """Claude and ChatGPT refresh for all their users from shared addresses;
    one budget per address would let one client exhaust another's."""
    from app.modules.mcp_access.api.oauth_routes import _by_client_and_address

    claude = await _by_client_and_address(
        _form_request(b"client_id=https%3A%2F%2Fclaude.ai%2Fdoc&grant_type=x")
    )
    chatgpt = await _by_client_and_address(
        _form_request(b"client_id=https%3A%2F%2Fchatgpt.com%2Fdoc&grant_type=x")
    )
    assert claude != chatgpt
    assert claude.startswith("203.0.113.9:")
    request = _form_request(b"client_id=abc&grant_type=x")
    await _by_client_and_address(request)
    # The handler after it still reads the same body.
    assert (await request.form()).get("grant_type") == "x"
