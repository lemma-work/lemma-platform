"""No secret in an install config reaches an API response, at any depth.

The redactor used to name the two places a secret was known to live. That is a
list which stays correct only until something adds a third, and RFC 7591
registration did: it writes a `client_secret` under `oauth`, a nesting the list
did not visit. Reading an install requires no particular role -- creating one
requires owner or editor -- so the OAuth client credential Lemma registered
with a tenant's authorization server was readable by any org member.

These cases are the nestings that exist today plus the shape rules, so the next
one is covered before it is written.
"""

from __future__ import annotations

from app.modules.connectors.api.auth_config_controller import _redact_config

MASK = "********"


def test_a_secret_nested_under_a_new_key_is_still_masked():
    """The regression itself: `oauth` was added by discovery, and no redaction
    rule had been taught about it."""
    out = _redact_config(
        {"oauth": {"client_id": "cid", "client_secret": "registered-secret"}}
    )
    assert out["oauth"]["client_secret"] == MASK
    assert out["oauth"]["client_id"] == "cid", "a client id is public by design"


def test_the_two_original_nestings_still_behave():
    out = _redact_config(
        {
            "client_secret": "top-level",
            "oauth2_credentials": {"client_id": "a", "client_secret": "b"},
        }
    )
    assert out["client_secret"] == MASK
    assert out["oauth2_credentials"]["client_secret"] == MASK
    assert out["oauth2_credentials"]["client_id"] == "a"


def test_a_pasted_token_is_masked():
    """An MCP install's `bearer_token` is the whole credential, and the old
    redactor returned it verbatim -- it only knew about client secrets."""
    assert _redact_config({"bearer_token": "sk-live-1"})["bearer_token"] == MASK


def test_a_credential_hidden_in_a_header_is_masked():
    """`extra_headers` is free-form, so a tenant can put their key anywhere in
    it. Recursion is what covers a shape nobody declared."""
    out = _redact_config(
        {"extra_headers": {"Authorization": "Bearer zzz", "X-Trace": "keep"}}
    )
    assert out["extra_headers"]["Authorization"] == MASK
    assert out["extra_headers"]["X-Trace"] == "keep"


def test_an_endpoint_is_a_location_not_a_credential():
    """`is_sensitive_key` matches substrings, so `token_endpoint` and
    `authorization_endpoint` look sensitive and are not. Masking them would
    hide what an operator needs to debug a discovery, and they are public --
    the authorization endpoint appears in the URL the person is sent to."""
    out = _redact_config(
        {
            "oauth": {
                "authorization_endpoint": "https://idp.example/authorize",
                "token_endpoint": "https://idp.example/token",
                "issuer": "https://idp.example",
            },
            "server_url": "https://server.example/mcp",
        }
    )
    assert out["oauth"]["authorization_endpoint"] == "https://idp.example/authorize"
    assert out["oauth"]["token_endpoint"] == "https://idp.example/token"
    assert out["server_url"] == "https://server.example/mcp"


def test_a_container_keeps_its_shape():
    """Masking a dict wholesale would break every client reading a field beside
    the secret, and `oauth2_credentials` matches the sensitive-key set itself."""
    out = _redact_config({"oauth2_credentials": {"client_id": "a"}})
    assert isinstance(out["oauth2_credentials"], dict)


def test_an_empty_secret_is_left_alone():
    """A blank is not a secret, and masking it would tell a reader a credential
    is set when none is."""
    assert _redact_config({"client_secret": ""})["client_secret"] == ""


def test_a_list_of_configs_is_walked():
    out = _redact_config({"servers": [{"bearer_token": "t1"}, {"name": "keep"}]})
    assert out["servers"][0]["bearer_token"] == MASK
    assert out["servers"][1]["name"] == "keep"


def test_none_stays_none():
    assert _redact_config(None) is None
