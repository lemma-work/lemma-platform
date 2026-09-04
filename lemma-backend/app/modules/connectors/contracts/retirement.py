"""Standing accounts down, as a public command.

A submodule rather than a name on `contracts/__init__` because importing it
pulls the SQLAlchemy model layer: every importer of any other contract would pay
for that, and the import budget says so out loud.
"""

from app.modules.connectors.services.account_retirement import (
    retire_accounts_for_tenant,
)

__all__ = ["retire_accounts_for_tenant"]
