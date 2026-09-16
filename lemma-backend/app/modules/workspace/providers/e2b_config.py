"""How this deployment talks to E2B.

Its own module so the provider file stays about behaviour. Everything here is
decided once at startup and read everywhere, and `metadata_namespace` is a
safety boundary rather than a preference -- which is easier to see when it is
not sharing a file with six hundred lines of lifecycle.

`CLOSED_TO_THE_INTERNET` is here for the opposite reason: it is the one thing
about reaching a sandbox that is deliberately *not* configurable, and it reads
as such next to the things that are.
"""

from __future__ import annotations

from dataclasses import dataclass


#: How every sandbox is created, and not a setting.
#:
#: E2B gives every port a sandbox listens on a public `*.e2b.app` name; there is
#: no private address to prefer instead, the way Docker has one. Closed, E2B
#: mints a per-sandbox traffic token and the edge answers 403 without it, and
#: `reach_port` hands that token to every caller -- so this is the nearest thing
#: to the private network the other fabric gets for free.
#:
#: It was a setting, `E2B_ALLOW_PUBLIC_TRAFFIC`, and being one bought nothing.
#: Nothing outside the backend ever needs a sandbox's own address: a browser is
#: handed a signed URL at *our* API and `port_proxy_controller` reverse-proxies
#: to the port. So the only thing the other value could do was expose whatever
#: was listening -- which includes the agent's browser and its dashboard, and
#: is exactly what `_require_private` then refuses to put a saved login into.
#: A knob whose every other position is a footgun is not configuration; it is a
#: paragraph of documentation warning you not to touch it.
#:
#: It belongs on `network` rather than being an argument of its own. `create`
#: types its remaining keywords as `Unpack[ApiParams]` and hands them to
#: `ConnectionConfig(**opts)`, which raises on a name it does not know -- a
#: top-level `allow_public_traffic=` is not ignored, it stops the sandbox being
#: created at all. `SandboxNetworkOpts` is `total=False`, so naming this one key
#: leaves egress exactly as it was.
CLOSED_TO_THE_INTERNET = {"allow_public_traffic": False}


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
