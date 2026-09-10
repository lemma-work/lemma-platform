"""GitHub-specific values other modules legitimately need.

A submodule rather than names on `contracts/__init__`: `install_url` reaches the
App auth service, and every importer of any other contract would pay for that
import. The budget gate says so out loud.
"""

from app.modules.connectors.config import connector_settings
from app.modules.connectors.services.auth.github_installation import (
    InstallState,
    install_url as github_install_url,
    installation_still_needed,
    manage_installation_url,
)


def install_state_for(connector_id: str, external_ref: str | None) -> InstallState:
    """How far an account is from reaching anything, without asking GitHub.

    The cheap half of the question, decided from the column alone, so listing
    accounts costs no network. It cannot tell `INSTALL_REQUIRED` apart from "you
    have several installations, pick one" -- that needs a call with the
    account's own token, which is what the installations endpoint is for.
    """
    return (
        InstallState.INSTALL_REQUIRED
        if installation_still_needed(connector_id, external_ref)
        else InstallState.READY
    )


__all__ = [
    "InstallState",
    "connector_settings",
    "github_install_url",
    "install_state_for",
    "installation_still_needed",
    "manage_installation_url",
]
