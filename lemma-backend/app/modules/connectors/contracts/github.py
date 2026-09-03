"""GitHub-specific values other modules legitimately need.

A submodule rather than names on `contracts/__init__`: `install_url` reaches the
App auth service, and every importer of any other contract would pay for that
import. The budget gate says so out loud.
"""

from app.modules.connectors.config import connector_settings
from app.modules.connectors.services.auth.github_installation import (
    install_url as github_install_url,
)

__all__ = ["connector_settings", "github_install_url"]
