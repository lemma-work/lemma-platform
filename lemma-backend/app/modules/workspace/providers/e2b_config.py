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

from collections.abc import Mapping
from dataclasses import dataclass, field

from app.core.log.log import get_logger
from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers.base import ProviderCreateSpec

logger = get_logger(__name__)


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
    # A workspace template per size a plan can buy, keyed `{cpu}x{memory_mb}`.
    # E2B fixes CPU and memory when a template is built, so a size is a
    # template. A size with no template here gets `workspace_template`.
    workspace_size_templates: Mapping[str, str] = field(default_factory=dict)


def lifecycle_for(kind: SandboxKind) -> dict[str, object]:
    """What E2B does to this sandbox when its timeout runs out.

    The SDK defaults `on_timeout` to `"kill"`, and this call used to pass no
    lifecycle at all -- so every workspace was created already scheduled for
    deletion, thirty minutes out, and on this provider deleting the sandbox
    deletes the user's files. Nothing in the row recorded it and nothing told
    the user; the only reason it was not a daily event is that the idle sweep
    usually paused the sandbox first, which stops the clock. A five-minute
    cron was the only thing standing between a long session and data loss.

    `keep_memory=False` matches what `release` already does, and for the same
    reason: a memory-preserving snapshot restores whatever was running,
    including a browser that had exhausted the sandbox, so the exhaustion
    became permanent across every later resume. It also rules out
    `auto_resume`, which E2B can only offer by restoring a memory snapshot in
    place. That trade is worth revisiting once a leak is impossible, and not
    before.

    Functions invert this: the leak was a *workspace* browser, while
    `lemma-function` runs function code and nothing else. Filesystem-only
    resumes a function sandbox *without* its runtime -- nothing re-runs the
    image CMD -- so it comes back answering 502, which is the P0.
    `test_e2b_function_liveness_real` measures both modes.
    """
    keep_memory = kind is SandboxKind.FUNCTION
    return {
        "on_timeout": {"action": "pause", "keep_memory": keep_memory},
        **({"auto_resume": True} if keep_memory else {}),
    }


def template_for(config: E2BProviderConfig, spec: ProviderCreateSpec) -> str:
    """The template this sandbox is built from.

    A workspace is built from the template for its plan's size. A size with no
    template configured is a deployment mistake, and refusing to start the
    person's workspace over it would punish them for it: it is logged, and the
    workspace is served at the default size.
    """
    if spec.kind is SandboxKind.FUNCTION:
        return config.function_template
    if spec.size is not None:
        sized = config.workspace_size_templates.get(spec.size.label)
        if sized:
            return sized
        logger.warning(
            "workspace.e2b.size_template_missing.degraded",
            sandbox_id=str(spec.sandbox_id),
            size=spec.size.label,
        )
    return config.workspace_template
