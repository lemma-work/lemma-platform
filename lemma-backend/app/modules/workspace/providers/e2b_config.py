"""How this deployment talks to E2B.

Its own module so the provider file stays about behaviour. Everything here is
decided once at startup and read everywhere, and two of the fields are safety
boundaries rather than preferences -- which is easier to see when they are not
sharing a file with six hundred lines of lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class E2BProviderConfig:
    api_key: str
    workspace_template: str
    function_template: str
    # Namespaces every metadata key this provider writes and queries, making a
    # provider blind to sandboxes labelled by another namespace. Required, not
    # defaulted: a shared default is what let two deployments on one E2B team
    # read each other's sandboxes as unowned orphans and destroy them. See
    # `provider_factory.resolve_metadata_namespace`.
    metadata_namespace: str
    # How long E2B keeps a sandbox alive without contact. The service touches
    # activity on use, so this is a backstop against leaking compute when the
    # backend dies, not the primary idle policy.
    sandbox_timeout_seconds: int = 60 * 30
    domain: str | None = None
    # Whether this sandbox's ports answer the internet without a credential.
    #
    # E2B gives every port a public `*.e2b.app` name. That was tolerable while
    # the only thing behind one was a dev server; it is not once a sandbox holds
    # a browser signed in as somebody. With this false, E2B mints a per-sandbox
    # traffic token and the edge answers 403 without it -- `reach_port` carries
    # the token, so nothing above the provider has to know.
    #
    # It is set at create and cannot be changed afterwards, so sandboxes made
    # before this flag stay open until they are replaced. `reach_port` reports
    # them as `public` rather than pretending otherwise.
    allow_public_traffic: bool = False
