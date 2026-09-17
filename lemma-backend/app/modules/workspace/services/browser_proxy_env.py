"""What a workspace sandbox is created with, to route its browser through a proxy.

Its own module rather than a function in `sandbox_service.py`: that file sits
right at the architecture ratchet's line ceiling, and provisioning env is a
separate concern from provisioning the sandbox itself -- `_provisioning_env`
has no dependency on anything else in that file beyond the settings singleton
every module in this package can already reach.
"""

from __future__ import annotations

from collections.abc import Sequence
import random

from pydantic import SecretStr

from app.modules.workspace.config import workspace_settings
from app.modules.workspace.domain.sandbox import SandboxKind


def provisioning_env(
    kind: SandboxKind, *, pool: Sequence[SecretStr] | None = None
) -> dict[str, str]:
    """Env baked into a sandbox at creation, before anything runs inside it.

    Only a workspace sandbox has a browser to proxy -- a function sandbox
    never opens one, so giving it a proxy assignment would spend nothing on
    anyone and cost a slot in the pool nobody reads. One choice per sandbox,
    made once here rather than by the browser at launch time: every restart
    of a resumed sandbox must open through the same proxy it was created
    with, or a session started through one residential IP would carry on
    through a different one after the sandbox's first pause.

    `pool` is a parameter, not a read of `workspace_settings` here, so a test
    supplies one directly instead of patching the settings singleton this
    module imports -- a constructor seam, used instead of a double planted on
    the module under test.
    """
    if kind != SandboxKind.WORKSPACE:
        return {}
    if pool is None:
        pool = workspace_settings.browser_proxy_urls
    if not pool:
        return {}
    # `AGENT_BROWSER_PROXY`, not a Lemma-owned name: agent-browser reads this
    # env var itself (falling back to it ahead of `HTTP_PROXY`/`ALL_PROXY`),
    # parses out any inline `user:pass@host:port`, and answers Chrome's CDP
    # `Fetch.authRequired` with the credentials -- so a credentialed proxy
    # works without this codebase touching CDP at all.
    return {"AGENT_BROWSER_PROXY": random.choice(pool).get_secret_value()}


__all__ = ["provisioning_env"]
