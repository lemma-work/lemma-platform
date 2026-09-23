"""How another module's HTTP client reaches a Lemma Desktop sandbox.

On Desktop the guest's sandbox addresses are reached over vsock rather than the
network; see `providers/desktop_tunnel.py`. A client that may address one takes
its transport from here, and gets httpx's default everywhere else.
"""

from __future__ import annotations

from app.modules.workspace.providers.desktop_tunnel import sandbox_transport

__all__ = ["sandbox_transport"]
