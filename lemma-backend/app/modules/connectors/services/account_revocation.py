"""Giving a credential up at the provider, when that is possible at all.

Deleting an account in Lemma does not by itself stop the token working
upstream, so this runs first. It is best-effort by design -- a provider that
will not revoke must not strand the rows in Lemma -- which makes it the kind of
code where a real failure hides easily, and did.
"""

from __future__ import annotations

from uuid import UUID

from app.core.log.log import get_logger
from app.modules.connectors.domain.account import OAuthCredentials
from app.modules.connectors.domain.auth_install import ResolvedAuthInstall
from app.modules.connectors.domain.ports import AuthProviderPort

logger = get_logger(__name__)


async def revoke_one(
    auth_provider: AuthProviderPort,
    *,
    install: ResolvedAuthInstall | None,
    credentials: OAuthCredentials,
    user_id: UUID,
) -> bool:
    """Ask the provider to drop this credential. True if it was asked at all.

    ``install`` is ``None`` when the connector has left the catalog. The
    provider needs it to know who it is revoking as, and Composio's
    implementation dereferences it -- previously into a broad except that
    logged and moved on, so the token stayed live at the provider and a log
    line was the only trace. Skipping is not better for the token, but it is
    honest about what happened and says so at a level production keeps.
    """
    if install is None:
        logger.info(
            "connectors.account_revocation.skipped_without_install",
            connector_id=None,
        )
        return False
    try:
        await auth_provider.revoke_connection(
            install=install, credentials=credentials, user_id=user_id
        )
        return True
    except Exception:
        logger.error("connectors.connector_service.revoke.failed", exc_info=True)
        return False
