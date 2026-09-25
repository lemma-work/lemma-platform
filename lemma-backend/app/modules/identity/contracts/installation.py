"""Whether this is a Desktop installation.

Published for host execution (docs/architecture/desktop-host-execution.md §2),
which exists only where the backend runs on the same computer as the Agent Host
it would route commands to. No account is special on that installation: who a
host runs commands for is its pairing, which the agent module owns.
"""

from __future__ import annotations

from app.modules.identity.config import identity_settings


def is_desktop_installation() -> bool:
    return identity_settings.is_desktop_installation()


__all__ = ["is_desktop_installation"]
