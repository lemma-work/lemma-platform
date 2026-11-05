"""Connectors: what an organization installs, what a person connects, and what
a pod runs through.

Three nouns, kept apart because the product keeps them apart. An *installation*
(an auth config) is the organization's. An *account* is one person's credential,
connected under an installation. A *pod connector* is a pod declaring that it
uses an installation, under a slug of its own, and saying whose account a call
runs as: each caller's own (``PER_MEMBER``) or one account its owner shared with
the pod (``SHARED``).

An operation only ever runs through a pod connector. There is no step that
names an account to run as, because there is no request that can: the account
is the connector's decision, never an argument.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from harness.connector_credentials import credential_matching, credential_schema_of
from harness.drivers.api import items_of
from harness.run import a_name_for
from harness.tenant import STANDING_CONNECTORS, standing_auth_config_name

JSON = dict[str, Any]

#: What a pod connector's slug may be: the API refuses anything else.
_NOT_SLUG = re.compile(r"[^a-z0-9_-]+")


@dataclass(frozen=True, slots=True)
class Refusal:
    """What the product told a client when it refused something.

    The code comes back as well as the status because the refusals that matter
    here share one: `CONNECTION_REQUIRED`, `REAUTH_REQUIRED`,
    `CONNECTOR_NEEDS_ACCOUNT` and `CONNECTOR_UNAVAILABLE` are all 409, and each
    asks a different person to do a different thing. A scenario that accepted
    any 409 could not tell "connect your account" from "the installation is
    disabled".
    """

    status: int
    code: str
    message: str = ""
    details: JSON = field(default_factory=dict)


def _refusal_of(response: Any) -> Refusal:
    try:
        body = response.json() if response.content else {}
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    details = body.get("details")
    return Refusal(
        status=response.status_code,
        code=str(body.get("code") or ""),
        message=str(body.get("message") or ""),
        details=details if isinstance(details, dict) else {},
    )


def slug_for(text: str) -> str:
    """A pod connector slug made from a connector id or a name."""
    slug = _NOT_SLUG.sub("-", text.lower()).strip("-_")
    return (slug or "connector")[:63]


class ConnectorSteps:
    """Mixed into :class:`harness.world.Person`."""

    # --- the catalogue ----------------------------------------------------

    async def available_connectors(self) -> list[JSON]:
        return items_of(await self.api.get("/connectors"))

    async def connector_status_of(self, organization: JSON) -> JSON:
        return await self.api.get(
            f"/organizations/{organization['id']}/connectors/status"
        )

    async def opens_connector(self, connector_id: str) -> JSON:
        return await self.api.get(f"/connectors/{connector_id}")

    async def skill_for(self, connector_id: str) -> Any:
        return await self.api.call("GET", f"/connectors/{connector_id}/skill")

    # --- installations ----------------------------------------------------

    async def installs_connector(
        self,
        connector_id: str,
        *,
        in_organization: JSON,
        named: str | None = None,
        kind: str | None = None,
    ) -> JSON:
        body: JSON = {
            "connector_id": connector_id,
            "name": named or a_name_for(connector_id),
        }
        if kind is not None:
            # Which of the connector's kinds. Optional when it offers one, and
            # decisive when it offers more: `gmail` is OAuth2 against a Google
            # client this deployment does not have under its own kind, and
            # Composio's managed OAuth app under "composio".
            body["kind"] = kind
        return await self.api.post(
            f"/organizations/{in_organization['id']}/connectors/auth-configs",
            what=f"{self.label} installing {connector_id!r}",
            json=body,
        )

    async def uses_connector(self, connector_id: str, *, in_organization: JSON) -> JSON:
        """The organization's standing auth config, found before it is made.

        The difference from `installs_connector` is what happens on the second
        run. An account belongs to the auth config it was consented against, so
        a fresh auth config every run discards every OAuth account every run —
        and re-consenting is the one step the suite cannot take for itself.
        Scenarios that want a *connected* provider go through here; scenarios
        about installing a connector still install their own, marked and
        disposable like everything else they make.
        """
        wanted = standing_auth_config_name(connector_id)
        for config in await self.auth_configs_in(in_organization):
            if config.get("name") == wanted:
                return config
        declared = next(
            (c for c in STANDING_CONNECTORS if c.connector == connector_id), None
        )
        return await self.installs_connector(
            connector_id,
            in_organization=in_organization,
            named=wanted,
            kind=declared.kind if declared else None,
        )

    async def installs_http_connector(
        self,
        *,
        in_organization: JSON,
        server_url: str,
        spec_url: str,
        named: str | None = None,
    ) -> JSON:
        """Install a connector for an API described by its own OpenAPI spec.

        The `http` kind is how anyone connects an internal or bespoke API, and
        it is the one connector kind a scenario can drive end to end without a
        third party — the provider is a server the suite runs itself.
        """
        return await self.api.post(
            f"/organizations/{in_organization['id']}/connectors/auth-configs",
            what=f"{self.label} installing an HTTP connector",
            json={
                "connector_id": "openapi",
                "kind": "http",
                "name": named or a_name_for("provider"),
                "config": {"server_url": server_url, "spec_url": spec_url},
            },
        )

    async def auth_configs_in(self, organization: JSON) -> list[JSON]:
        return items_of(
            await self.api.get(
                f"/organizations/{organization['id']}/connectors/auth-configs"
            )
        )

    async def opens_auth_config(
        self, auth_config: JSON, *, in_organization: JSON
    ) -> JSON:
        return await self.api.get(
            f"/organizations/{in_organization['id']}/connectors/auth-configs/"
            f"{auth_config['name']}"
        )

    async def renames_auth_config(
        self, auth_config: JSON, *, to: str, in_organization: JSON
    ) -> JSON:
        return await self.api.patch(
            f"/organizations/{in_organization['id']}/connectors/auth-configs/"
            f"{auth_config['name']}",
            what=f"{self.label} renaming an installation",
            json={"name": to},
        )

    async def what_uninstalling_would_stop(
        self, auth_config: JSON, *, in_organization: JSON
    ) -> JSON:
        """The pods and accounts an installation's removal would take with it."""
        return await self.api.get(
            f"/organizations/{in_organization['id']}/installations/"
            f"{auth_config['id']}/impact",
        )

    async def uninstalls_connector(
        self, auth_config: JSON, *, in_organization: JSON
    ) -> None:
        await self.api.delete(
            f"/organizations/{in_organization['id']}/connectors/auth-configs/"
            f"{auth_config['name']}",
            what=f"{self.label} uninstalling a connector",
        )

    async def operations_of(
        self, auth_config: JSON, *, in_organization: JSON
    ) -> list[JSON]:
        return items_of(
            await self.api.get(
                f"/organizations/{in_organization['id']}/connectors/"
                f"{auth_config['name']}/operations"
            )
        )

    async def operation_detail(
        self, name: str, *, auth_config: JSON, in_organization: JSON
    ) -> JSON:
        return await self.api.get(
            f"/organizations/{in_organization['id']}/connectors/"
            f"{auth_config['name']}/operations/{name}"
        )

    async def operation_details(
        self, names: list[str], *, auth_config: JSON, in_organization: JSON
    ) -> Any:
        return await self.api.call(
            "POST",
            f"/organizations/{in_organization['id']}/connectors/"
            f"{auth_config['name']}/operations/details",
            json={"operation_names": names},
        )

    async def refreshes_operations(
        self, auth_config: JSON, *, in_organization: JSON
    ) -> Any:
        return await self.api.call(
            "POST",
            f"/organizations/{in_organization['id']}/connectors/auth-configs/"
            f"{auth_config['name']}/operations/refresh",
        )

    async def triggers_of(
        self, auth_config: JSON, *, in_organization: JSON
    ) -> list[JSON]:
        return items_of(
            await self.api.get(
                f"/organizations/{in_organization['id']}/connectors/"
                f"{auth_config['name']}/triggers"
            )
        )

    # --- a person's own accounts -------------------------------------------

    async def account_for(
        self, connector_id: str, *, in_organization: JSON
    ) -> JSON | None:
        """The connected account for a connector, if a person has made one."""
        for account in await self.accounts_in(in_organization):
            if account.get("connector_id") == connector_id and (
                account.get("status") == "CONNECTED"
            ):
                return account
        return None

    async def connects_account(
        self,
        *,
        in_organization: JSON,
        auth_config: JSON,
        credentials: JSON,
        provider_account_id: str | None = None,
    ) -> JSON:
        body: JSON = {
            "auth_config_id": str(auth_config["id"]),
            "credentials": credentials,
        }
        if provider_account_id:
            body["provider_account_id"] = provider_account_id
        return await self.api.post(
            f"/organizations/{in_organization['id']}/connectors/accounts",
            what=f"{self.label} connecting an account",
            json=body,
        )

    async def credential_for(self, auth_config: JSON, *, holding: str) -> JSON:
        """A credential this install's connector accepts, carrying ``holding``.

        Asked of the catalogue, because the shape is the connector kind's: an
        HTTP connector spends ``access_token``, Telegram requires ``bot_token``
        and refuses anything else. See `harness.connector_credentials`.
        """
        connector = await self.opens_connector(str(auth_config["connector_id"]))
        schema = credential_schema_of(connector, auth_config.get("kind"))
        return credential_matching(schema, holding)

    async def accounts_in(self, organization: JSON) -> list[JSON]:
        return items_of(
            await self.api.get(f"/organizations/{organization['id']}/connectors/accounts")
        )

    async def deletes_account(self, account: JSON, *, in_organization: JSON) -> None:
        await self.api.delete(
            f"/organizations/{in_organization['id']}/connectors/accounts/{account['id']}"
        )

    async def starts_connecting(self, *, in_organization: JSON, auth_config: JSON) -> Any:
        return await self.api.call(
            "POST",
            f"/organizations/{in_organization['id']}/connectors/connect-requests",
            json={"auth_config_id": str(auth_config["id"])},
        )

    # --- a pod's connectors ------------------------------------------------

    async def declares_pod_connector(
        self,
        auth_config: JSON,
        *,
        in_pod: JSON,
        slug: str | None = None,
        mode: str = "PER_MEMBER",
        runs_as: JSON | None = None,
        required: bool = False,
        purpose: str | None = None,
    ) -> JSON:
        """Declare that the pod uses an installation, under a slug of its own.

        ``mode`` is the whole of whose account a call uses: ``PER_MEMBER`` is
        each caller's own, ``SHARED`` is ``runs_as`` — a pod account its owner
        has already shared with the pod. An editor picks among shared accounts;
        nothing here can make one shared.
        """
        body: JSON = {
            "slug": slug or slug_for(str(auth_config.get("connector_id") or "")),
            "auth_config_id": str(auth_config["id"]),
            "mode": mode,
            "required": required,
        }
        if runs_as is not None:
            body["shared_pod_account_id"] = str(runs_as["id"])
        if purpose is not None:
            body["purpose"] = purpose
        return await self.api.post(
            f"/pods/{in_pod['id']}/connectors",
            status=201,
            what=f"{self.label} declaring pod connector {body['slug']!r} ({mode})",
            json=body,
        )

    async def is_refused_declaring_pod_connector(
        self,
        auth_config: JSON,
        *,
        in_pod: JSON,
        slug: str,
        mode: str = "PER_MEMBER",
        runs_as: JSON | None = None,
    ) -> Refusal:
        body: JSON = {
            "slug": slug,
            "auth_config_id": str(auth_config["id"]),
            "mode": mode,
        }
        if runs_as is not None:
            body["shared_pod_account_id"] = str(runs_as["id"])
        response = await self.api.call(
            "POST", f"/pods/{in_pod['id']}/connectors", json=body
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused declaring pod "
                f"connector {slug!r} ({mode}), but it succeeded "
                f"({response.status_code})"
            )
        return _refusal_of(response)

    async def pod_connectors_in(self, pod: JSON) -> list[JSON]:
        return items_of(await self.api.get(f"/pods/{pod['id']}/connectors"))

    async def opens_pod_connector(self, connector: JSON, *, in_pod: JSON) -> JSON:
        return await self.api.get(f"/pods/{in_pod['id']}/connectors/{connector['slug']}")

    async def operations_through(self, connector: JSON, *, in_pod: JSON) -> list[JSON]:
        """What a pod connector offers, as a member of the pod sees it."""
        return items_of(
            await self.api.get(
                f"/pods/{in_pod['id']}/connectors/{connector['slug']}/operations"
            )
        )

    async def operation_through(
        self, name: str, *, connector: JSON, in_pod: JSON
    ) -> JSON:
        return await self.api.get(
            f"/pods/{in_pod['id']}/connectors/{connector['slug']}/operations/{name}"
        )

    async def runs_operation(
        self, name: str, *, through: JSON, in_pod: JSON, payload: JSON
    ) -> JSON:
        """Run an operation through a pod connector, as whoever this person is.

        Takes no account. Which account the call runs as is the connector's
        decision, and that is the property half this suite's connector
        scenarios are about.
        """
        return await self.api.post(
            _execute_path(in_pod, through, name),
            what=f"{self.label} running {name!r} through {through['slug']!r}",
            json={"payload": payload},
        )

    async def is_refused_running_operation(
        self, name: str, *, through: JSON, in_pod: JSON, payload: JSON
    ) -> Refusal:
        response = await self.api.call(
            "POST", _execute_path(in_pod, through, name), json={"payload": payload}
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused running {name!r} "
                f"through {through['slug']!r}, but it succeeded "
                f"({response.status_code})"
            )
        return _refusal_of(response)

    # --- the accounts in a pod --------------------------------------------

    async def adds_account_to_pod(
        self, account: JSON, *, in_pod: JSON, visibility: str = "PERSONAL"
    ) -> JSON:
        """Bring one of this person's own accounts into a pod.

        ``PERSONAL`` serves only its owner's calls. ``POD`` shares it with every
        member, which only its owner may do — and is what a ``SHARED``
        connector needs before it can run as it.
        """
        return await self.api.post(
            f"/pods/{in_pod['id']}/connector-accounts",
            status=201,
            what=f"{self.label} adding an account to the pod as {visibility}",
            json={"account_id": str(account["id"]), "visibility": visibility},
        )

    async def shares_pod_account(
        self, pod_account: JSON, *, in_pod: JSON, visibility: str = "POD"
    ) -> JSON:
        return await self.api.patch(
            f"/pods/{in_pod['id']}/connector-accounts/{pod_account['id']}",
            what=f"{self.label} sharing a pod account as {visibility}",
            json={"visibility": visibility},
        )

    async def withdraws_pod_account(self, pod_account: JSON, *, in_pod: JSON) -> JSON:
        """Take a shared account back to its owner alone (PERSONAL)."""
        return await self.shares_pod_account(
            pod_account, in_pod=in_pod, visibility="PERSONAL"
        )

    async def is_refused_sharing_pod_account(
        self, pod_account: JSON, *, in_pod: JSON, visibility: str = "POD"
    ) -> Refusal:
        response = await self.api.call(
            "PATCH",
            f"/pods/{in_pod['id']}/connector-accounts/{pod_account['id']}",
            json={"visibility": visibility},
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused sharing somebody "
                f"else's account, but it succeeded ({response.status_code})"
            )
        return _refusal_of(response)

    async def pod_accounts_in(self, pod: JSON) -> list[JSON]:
        """The pod's accounts this person can see: their own, and shared ones."""
        return items_of(await self.api.get(f"/pods/{pod['id']}/connector-accounts"))

    async def shares_as_pod_connector(
        self, account: JSON, *, in_pod: JSON, slug: str | None = None
    ) -> JSON:
        """Share one's own account with a pod, and run a SHARED connector as it.

        What a bot-backed surface needs: it answers with nobody at the keyboard,
        so it runs as one account its owner put in the pod for everybody.
        Idempotent on a pod that stands between runs — a connector already
        running as this account is returned rather than declared again, and a
        slug already taken by something else is not reused.
        """
        pod_account = await self.adds_account_to_pod(
            account, in_pod=in_pod, visibility="POD"
        )
        existing = await self.pod_connectors_in(in_pod)
        for connector in existing:
            if str(connector.get("shared_pod_account_id")) == str(pod_account["id"]):
                return connector
        base = slug or slug_for(str(account.get("connector_id") or "connector"))
        taken = {str(connector.get("slug")) for connector in existing}
        chosen, n = base, 2
        while chosen in taken:
            chosen, n = f"{base}-{n}", n + 1
        return await self.declares_pod_connector(
            {"id": account["auth_config_id"]},
            in_pod=in_pod,
            slug=chosen,
            mode="SHARED",
            runs_as=pod_account,
        )


def _execute_path(pod: JSON, connector: JSON, operation: str) -> str:
    return (
        f"/pods/{pod['id']}/connectors/{connector['slug']}/operations/{operation}/execute"
    )
