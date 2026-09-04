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

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from app.modules.connectors.api.auth_config_controller import (
    _redact_config,
    get_auth_config,
    list_auth_configs,
)
from app.modules.connectors.domain.auth_config import (
    AuthConfigEntity,
    AuthConfigSource,
)

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


def test_every_header_value_is_masked_whatever_the_header_is_called():
    """`extra_headers` and `default_headers` are the two maps whose *keys* the
    tenant chooses -- both are documented as "anything the server needs beyond
    the token below". `Authorization` matches the sensitive-key set, but
    `X-Auth`, `X-Signature-Key` and `Cookie2` do not and are the same thing, so
    over these two maps a key-name rule can never be complete. The values are
    masked by position instead; the names stay, so an operator can still see
    which headers an install sends."""
    out = _redact_config(
        {
            "extra_headers": {"Authorization": "Bearer zzz", "X-Signature-Key": "shh"},
            "default_headers": {"X-Auth": "hunter2"},
        }
    )
    assert out["extra_headers"] == {
        "Authorization": MASK,
        "X-Signature-Key": MASK,
    }
    assert out["default_headers"] == {"X-Auth": MASK}


def test_an_unset_header_is_not_reported_as_set():
    """Same rule as an empty client secret: masking a blank would tell the
    reader a value is configured when none is."""
    out = _redact_config({"extra_headers": {"X-Env": "", "X-Trace": None}})
    assert out["extra_headers"] == {"X-Env": "", "X-Trace": None}


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


class TestOnlyAManagerSeesTheConfig:
    """Masking secrets by key name cannot be the only control over a map whose
    key names the tenant chooses, so the read is levelled with the write.

    Which installs exist, and whether they are healthy, stays visible to every
    member -- that is what the connectors page is for. The configuration is
    not: for the mcp/http/sql kinds it is entirely tenant-written, and creating
    it already requires owner or editor.
    """

    @staticmethod
    def _install() -> AuthConfigEntity:
        return AuthConfigEntity(
            id=uuid4(),
            organization_id=uuid4(),
            connector_id="mcp",
            provider="LEMMA",
            config_source=AuthConfigSource.SYSTEM_DEFAULT,
            name="an-install",
            config={
                "server_url": "https://internal.example/mcp",
                "extra_headers": {"X-Auth": "hunter2"},
            },
        )

    @staticmethod
    def _service(*, may_read: bool) -> AsyncMock:
        install = TestOnlyAManagerSeesTheConfig._install()
        return AsyncMock(
            list_auth_configs=AsyncMock(return_value=([install], None)),
            get_auth_config_by_name=AsyncMock(return_value=install),
            may_read_install_config=AsyncMock(return_value=may_read),
            # Stated rather than left to the mock's defaults: the response now
            # carries the install's resolved auth scheme, and an AsyncMock's
            # auto-generated child would hand the schema a coroutine.
            install_auth_schemes=AsyncMock(return_value={install.id: "API_KEY"}),
        )

    async def test_a_manager_still_sees_it(self):
        response = await get_auth_config(
            user=Mock(id=uuid4()),
            organization_id=uuid4(),
            auth_config_name="an-install",
            connector_service=self._service(may_read=True),
        )

        assert response.config is not None
        assert response.config["server_url"] == "https://internal.example/mcp"
        assert response.config["extra_headers"] == {"X-Auth": MASK}

    async def test_a_plain_member_sees_the_install_but_not_its_config(self):
        response = await get_auth_config(
            user=Mock(id=uuid4()),
            organization_id=uuid4(),
            auth_config_name="an-install",
            connector_service=self._service(may_read=False),
        )

        assert response.name == "an-install"
        assert response.status, "which installs exist stays visible"
        assert response.config is None
        assert response.auth_scheme == "API_KEY", (
            "how to connect an install is not part of what is withheld -- it is "
            "the one thing config held that a member still has to know"
        )

    async def test_the_list_applies_the_same_rule(self):
        listing = await list_auth_configs(
            user=Mock(id=uuid4()),
            organization_id=uuid4(),
            connector_service=self._service(may_read=False),
            limit=100,
            page_token=None,
        )

        assert [item.config for item in listing.items] == [None]
