"""The Lemma addresses a process on the user's own machine is given.

A sandbox's environment names the backend the way the sandbox's network
reaches it -- on Desktop `host.lemma.internal`, which only the guest's
containers resolve. A process on the Mac itself -- a host-execution command, or
a coding agent the Agent Host runs -- needs the addresses this machine can
reach, from the same perspective the CLI is configured from.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.config import settings
from app.modules.workspace.config import workspace_settings


def host_reachable_addresses() -> dict[str, str]:
    """Where the backend is reachable from the machine the host process runs on."""
    addresses = {
        "LEMMA_BASE_URL": settings.cli_api_url or settings.api_url,
        "LEMMA_AUTH_URL": settings.cli_auth_frontend_url or settings.auth_frontend_url,
        "LEMMA_HOST_ORIGIN": settings.frontend_url,
    }
    return {name: value for name, value in addresses.items() if value}


def with_host_addresses(environment: Mapping[str, str]) -> dict[str, str]:
    """``environment`` with its Lemma addresses replaced by host-reachable ones.

    `LEMMA_WORKSPACE_URL` goes: it addresses the VM sandbox's runtime, and a
    process on the Mac that followed it would be working on a filesystem that
    is not the folder it was given.
    """
    kept = {
        name: value
        for name, value in environment.items()
        if name != "LEMMA_WORKSPACE_URL"
    }
    return kept | host_reachable_addresses()


def host_cli_root() -> str | None:
    """The folder holding the `lemma` CLI this backend ships beside it, if any.

    Desktop only (`WORKSPACE_HOST_CLI_ROOT`, set by the host pack): processes on
    the user's Mac -- host-execution commands and the coding agents the Agent
    Host starts -- find its `bin/lemma` first on their PATH, if the Mac accepts
    the folder (docs/architecture/desktop-host-execution.md §6).
    """
    return workspace_settings.host_cli_root or None


__all__ = ["host_cli_root", "host_reachable_addresses", "with_host_addresses"]
