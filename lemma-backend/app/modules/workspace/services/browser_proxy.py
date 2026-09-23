"""Whether a sandbox's browser goes through a proxy, and which one.

The server's decision, re-asserted at every browser start. It used to be
baked into the sandbox's creation environment, which meant it could be given
and never withdrawn: clearing the pool left every existing sandbox proxied
until it was replaced, and workspace sandboxes are not replaced on drift.

So the decision travels as a one-line file the backend writes into the
sandbox, and `lemma-ensure-display` reads it on every run. An empty file is
a decision -- "no proxy" -- and is how a withdrawal reaches a sandbox that
already has one. Measured on the image: a decision of `p1` then `p2` then
empty produced exactly those three states on Chrome's command line, and a
stale `AGENT_BROWSER_PROXY` baked into an older sandbox no longer wins,
because the script unsets it.
"""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
from uuid import UUID

from pydantic import SecretStr

from app.modules.workspace.config import workspace_settings
from app.modules.workspace.domain.sandbox import SandboxKind

#: Where the sandbox reads the decision. Outside `/tmp/lemma-browser`, which
#: the quiescer deletes wholesale before a pause -- the same reasoning that
#: puts the relay token at `/tmp/lemma-relay/token`.
BROWSER_PROXY_DECISION_PATH = "/tmp/lemma-browser-policy/proxy"


def browser_proxy_for(
    sandbox_id: UUID,
    kind: SandboxKind,
    *,
    pool: Sequence[SecretStr] | None = None,
) -> SecretStr | None:
    """Which proxy this sandbox uses, or None for a direct connection.

    Only a workspace sandbox has a browser to proxy; a function sandbox
    never opens one.

    **Sticky by construction, not by accident.** This was `random.choice`
    made once at create, and the docstring defended that because "every
    restart of a resumed sandbox must open through the same proxy it was
    created with" -- which was true, and which the create-time mechanism
    only delivered until the sandbox was replaced. Rendezvous hashing on the
    sandbox id gives the same answer every time, across restarts, resumes
    *and* replacement, with no stored state.

    It also behaves well when the pool changes, which modulo would not:
    removing an entry moves only the sandboxes that held it, and adding one
    moves roughly a 1/n share. A sandbox's exit IP changes when the operator
    changes the pool, and not otherwise.

    Stickiness matters because this exists for sign-in pages: a session
    cookie bound to an IP logs the person out when the IP hops, and a
    residential proxy that moves mid-task scores worse with bot detection
    than a stable one. It is also what Bright Data and its like sell.

    `pool` is a parameter rather than a read of `workspace_settings` here so
    a test supplies one directly instead of patching the settings singleton
    -- a constructor seam, not a double planted on the module under test.
    """
    if kind != SandboxKind.WORKSPACE:
        return None
    if pool is None:
        pool = workspace_settings.browser_proxy_urls
    if not pool:
        return None
    return max(pool, key=lambda url: _weight(sandbox_id, url))


def _weight(sandbox_id: UUID, url: SecretStr) -> bytes:
    """The rendezvous score for one (sandbox, proxy) pair.

    A digest rather than a plain hash: `hash()` is salted per process, so
    two API workers would disagree about which proxy a sandbox uses and the
    exit IP would depend on which one served the request.
    """
    return hashlib.sha256(sandbox_id.bytes + url.get_secret_value().encode()).digest()


def decision_bytes(proxy: SecretStr | None) -> bytes:
    """The file's contents: the URL, or empty for "no proxy".

    Empty rather than absent, because the sandbox has to be able to tell
    "the server says no proxy" from "the server has not said". A file that
    is merely missing is the second, and an older sandbox that still has a
    baked environment variable needs the first.
    """
    return b"" if proxy is None else proxy.get_secret_value().encode()


__all__ = [
    "BROWSER_PROXY_DECISION_PATH",
    "browser_proxy_for",
    "decision_bytes",
]
