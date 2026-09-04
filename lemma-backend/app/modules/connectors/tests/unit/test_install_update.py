"""What an update to an install does to the accounts hanging off it.

The classification is the whole risk. Too eager and a typo fix signs out every
user in the org; too lax and their accounts look healthy while every call 401s.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.modules.connectors.domain.connector import ConnectorKind
from app.modules.connectors.services.install_update import (
    config_change_effects,
    renegotiated_authorization,
)


def _effects(kind, before, after):
    return config_change_effects(kind=kind, before=before, after=after)


class TestMcpAndHttpTargets:
    def test_an_unchanged_config_changes_nothing(self):
        config = {"server_url": "https://mcp.example.com/mcp"}
        assert _effects(ConnectorKind.MCP, config, dict(config)) == (False, False)

    def test_moving_to_a_different_host_invalidates_credentials(self):
        # The bearer token was issued by whoever runs the old server. It says
        # nothing about the new one.
        rediscover, invalidates = _effects(
            ConnectorKind.MCP,
            {"server_url": "https://old.example.com/mcp"},
            {"server_url": "https://new.example.com/mcp"},
        )
        assert (rediscover, invalidates) == (True, True)

    def test_correcting_the_path_on_the_same_server_keeps_accounts(self):
        # Same origin, so the token still applies. Signing everyone out over a
        # path typo is exactly the behaviour that made delete-and-recreate the
        # only option before.
        rediscover, invalidates = _effects(
            ConnectorKind.MCP,
            {"server_url": "https://mcp.example.com/sse"},
            {"server_url": "https://mcp.example.com/mcp"},
        )
        assert rediscover is True
        assert invalidates is False

    def test_a_different_port_on_the_same_host_is_a_different_target(self):
        _, invalidates = _effects(
            ConnectorKind.MCP,
            {"server_url": "https://mcp.example.com:8443/mcp"},
            {"server_url": "https://mcp.example.com:9443/mcp"},
        )
        assert invalidates is True

    def test_upgrading_the_scheme_is_a_different_target(self):
        # http -> https is a different origin, and a token sent to one was not
        # necessarily issued for the other.
        _, invalidates = _effects(
            ConnectorKind.MCP,
            {"server_url": "http://mcp.example.com/mcp"},
            {"server_url": "https://mcp.example.com/mcp"},
        )
        assert invalidates is True

    def test_changing_only_headers_rediscovers_without_touching_accounts(self):
        rediscover, invalidates = _effects(
            ConnectorKind.MCP,
            {"server_url": "https://mcp.example.com/mcp", "extra_headers": {}},
            {
                "server_url": "https://mcp.example.com/mcp",
                "extra_headers": {"X-Env": "prod"},
            },
        )
        assert rediscover is True
        assert invalidates is False

    def test_pointing_http_at_a_new_spec_rediscovers(self):
        rediscover, _ = _effects(
            ConnectorKind.HTTP,
            {
                "server_url": "https://api.example.com",
                "spec_url": "https://api.example.com/v1.json",
            },
            {
                "server_url": "https://api.example.com",
                "spec_url": "https://api.example.com/v2.json",
            },
        )
        assert rediscover is True


class TestSqlTargets:
    @pytest.mark.parametrize(
        "field,value", [("host", "db2.example.com"), ("database", "other")]
    )
    def test_a_different_database_invalidates_credentials(self, field, value):
        # A username/password pair is defined inside one database on one host.
        base = {"host": "db.example.com", "port": 5432, "database": "app"}
        _, invalidates = _effects(ConnectorKind.SQL, base, {**base, field: value})
        assert invalidates is True

    def test_sql_never_rediscovers(self):
        # Its operations are a fixed set, not something a server advertises.
        base = {"host": "db.example.com", "port": 5432, "database": "app"}
        rediscover, _ = _effects(
            ConnectorKind.SQL, base, {**base, "host": "db2.example.com"}
        )
        assert rediscover is False


class TestOAuthClientSwap:
    def test_swapping_the_org_oauth_app_invalidates_every_token(self):
        # Tokens are bound to the client that issued them.
        _, invalidates = _effects(
            ConnectorKind.PACKAGE,
            {"oauth2_credentials": {"client_id": "old", "client_secret": "s1"}},
            {"oauth2_credentials": {"client_id": "new", "client_secret": "s2"}},
        )
        assert invalidates is True

    def test_rotating_only_the_secret_still_invalidates(self):
        _, invalidates = _effects(
            ConnectorKind.PACKAGE,
            {"oauth2_credentials": {"client_id": "same", "client_secret": "old"}},
            {"oauth2_credentials": {"client_id": "same", "client_secret": "new"}},
        )
        assert invalidates is True

    def test_flat_credentials_are_compared_too(self):
        # Both shapes are part of the existing API.
        _, invalidates = _effects(
            ConnectorKind.COMPOSIO,
            {"client_id": "old", "client_secret": "s"},
            {"client_id": "new", "client_secret": "s"},
        )
        assert invalidates is True

    def test_an_unrelated_key_leaves_accounts_alone(self):
        _, invalidates = _effects(
            ConnectorKind.PACKAGE,
            {
                "oauth2_credentials": {"client_id": "same", "client_secret": "s"},
                "label": "a",
            },
            {
                "oauth2_credentials": {"client_id": "same", "client_secret": "s"},
                "label": "b",
            },
        )
        assert invalidates is False


class TestEmptyConfigs:
    def test_none_to_none_is_a_no_op(self):
        assert _effects(ConnectorKind.MCP, None, None) == (False, False)

    def test_none_and_empty_dict_are_the_same_change(self):
        assert _effects(ConnectorKind.MCP, None, {}) == (False, False)


class TestRenegotiatingMcpAuthorization:
    """Where the credential is going to be *issued*, once the target moves.

    Marking every account REAUTH_REQUIRED is only half a repair. The other half
    is that the reconnect has somewhere valid to go, and the `oauth` block the
    merge preserves names the server that was replaced.
    """

    @staticmethod
    def _stored(server: str) -> dict:
        return {
            "server_url": f"https://{server}/mcp",
            "oauth": {
                "issuer": f"https://{server}",
                "authorization_endpoint": f"https://{server}/authorize",
                "token_endpoint": f"https://{server}/token",
                "resource": f"https://{server}/mcp",
                "scopes": [],
                "client_id": "old-client",
                "client_secret": "old-secret",
            },
        }

    async def _renegotiate(self, before, after, negotiate):
        with patch(
            "app.modules.connectors.services.install_update"
            ".negotiate_mcp_authorization",
            negotiate,
        ):
            return await renegotiated_authorization(
                kind=ConnectorKind.MCP,
                before=before,
                after=after,
                redirect_uri="https://lemma.example/callback",
            )

    async def test_a_new_origin_re_registers_against_the_new_server(self):
        before = self._stored("old.example.com")
        after = {**before, "server_url": "https://new.example.com/mcp"}
        seen: dict = {}

        async def negotiate(*, kind, config, redirect_uri):
            seen.update(config)
            return {**config, "oauth": {"issuer": "https://new.example.com"}}

        result = await self._renegotiate(before, after, negotiate)

        # The stale block never reaches the negotiator: it returns the config
        # untouched when one is already present, so leaving it in would make
        # the call a no-op and preserve exactly what has to go.
        assert "oauth" not in seen
        assert result["oauth"] == {"issuer": "https://new.example.com"}

    async def test_a_server_that_wants_no_oauth_is_left_with_none(self):
        before = self._stored("old.example.com")
        after = {**before, "server_url": "https://new.example.com/mcp"}

        async def negotiate(*, kind, config, redirect_uri):
            return config

        result = await self._renegotiate(before, after, negotiate)

        # A paste-a-token install is the honest outcome. Keeping the old
        # server's issuer would send people to a consent screen that cannot
        # produce a credential the new server accepts.
        assert "oauth" not in result

    async def test_a_path_correction_keeps_the_registered_client(self):
        before = self._stored("mcp.example.com")
        after = {**before, "server_url": "https://mcp.example.com/sse"}
        called = False

        async def negotiate(*, kind, config, redirect_uri):
            nonlocal called
            called = True
            return config

        result = await self._renegotiate(before, after, negotiate)

        assert called is False
        assert result["oauth"]["client_id"] == "old-client"

    async def test_an_oauth_block_supplied_with_the_edit_is_left_alone(self):
        before = self._stored("old.example.com")
        supplied = {"issuer": "https://chosen.example.com"}
        after = {
            **before,
            "server_url": "https://new.example.com/mcp",
            "oauth": supplied,
        }
        called = False

        async def negotiate(*, kind, config, redirect_uri):
            nonlocal called
            called = True
            return config

        result = await self._renegotiate(before, after, negotiate)

        assert called is False
        assert result["oauth"] is supplied

    async def test_a_non_mcp_kind_is_never_renegotiated(self):
        called = False

        async def negotiate(*, kind, config, redirect_uri):
            nonlocal called
            called = True
            return config

        with patch(
            "app.modules.connectors.services.install_update"
            ".negotiate_mcp_authorization",
            negotiate,
        ):
            result = await renegotiated_authorization(
                kind=ConnectorKind.HTTP,
                before={"server_url": "https://old.example.com"},
                after={"server_url": "https://new.example.com"},
                redirect_uri="https://lemma.example/callback",
            )

        assert called is False
        assert result == {"server_url": "https://new.example.com"}
