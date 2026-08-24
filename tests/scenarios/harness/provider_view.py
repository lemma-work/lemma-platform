"""A third-party API for a connector to be pointed at, read back from the proxy.

The same shape scenarios got from `FakeProvider` — a base URL, a spec URL, and
the calls that arrived — except the server is not here. It runs inside the
egress proxy, which answers for `provider.scenarios.example`.

The hostname matters more than it looks. It is reserved (RFC 6761), so it
resolves nowhere, and the product's URL guard permits an unresolvable host under
`testing` while still refusing anything that resolves into private space. That
is what lets a connector be installed and executed against it with the SSRF
guard at full production strictness — where the old loopback server needed the
guard switched off for the entire run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The name the connector is installed against. Reserved, so nothing resolves
#: it; the proxy is what answers.
PROVIDER_HOST = "provider.scenarios.example"
PROVIDER_BASE = f"https://{PROVIDER_HOST}"


@dataclass(frozen=True, slots=True)
class ReceivedCall:
    """One request that reached the provider."""

    method: str
    path: str
    body: Any
    authorization: str


@dataclass(frozen=True, slots=True)
class ProviderView:
    """The provider side of a scenario, as the proxy saw it."""

    egress: Any

    @property
    def base_url(self) -> str:
        return PROVIDER_BASE

    @property
    def spec_url(self) -> str:
        return f"{PROVIDER_BASE}/openapi.json"

    def calls_to(self, path: str) -> list[ReceivedCall]:
        """Requests whose path contains `path`, oldest first."""
        return [
            ReceivedCall(
                method=call.method,
                path=call.path,
                body=call.json_body(),
                authorization=call.authorization,
            )
            for call in self.egress.calls_to(PROVIDER_HOST, path_contains=path)
        ]

    @property
    def received(self) -> list[ReceivedCall]:
        """Everything that reached the provider."""
        return self.calls_to("")

    def clear(self) -> None:
        self.egress.forget()
