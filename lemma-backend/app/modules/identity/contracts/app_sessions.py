"""What an app host needs to know about the browser session behind its cookie."""

import asyncio
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from supertokens_python.recipe.session.asyncio import get_session_information

from app.core.config import settings
from app.modules.identity.config import identity_settings


def app_sign_in_url() -> str:
    """The configured browser portal, including its sign-in base path."""
    origin = urlsplit(settings.auth_frontend_url)
    path = origin.path.rstrip("/") or identity_settings.auth_website_base_path
    return urlunsplit((origin.scheme, origin.netloc, path, origin.query, ""))


async def session_is_live_for(handle: str, user_id: UUID) -> bool:
    """Whether session ``handle`` still exists, belongs to ``user_id`` and is unexpired.

    Asks the session service rather than trusting a token, so a sign-out counts.
    Raises when the service cannot answer: unknown is not the same as revoked.
    """
    async with asyncio.timeout(5):
        information = await get_session_information(handle)
    return (
        information is not None
        and information.user_id == str(user_id)
        and information.expiry > int(time.time() * 1000)
    )
