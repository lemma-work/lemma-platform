"""Whether an app asset session's parent browser identity is still eligible."""

import asyncio
import time
from uuid import UUID
from urllib.parse import urlsplit, urlunsplit
from starlette.types import Scope

from supertokens_python.recipe.session.asyncio import get_session_information

from app.core.config import settings
from app.modules.identity.config import identity_settings
from app.modules.identity.infrastructure.supertokens_auth.helpers import (
    _assert_local_user_can_authenticate,
)
from app.modules.identity.services.auth_abuse import client_ip


def app_sign_in_url() -> str:
    """The configured browser portal, including its sign-in base path."""
    origin = urlsplit(settings.auth_frontend_url)
    path = origin.path.rstrip("/") or identity_settings.auth_website_base_path
    return urlunsplit((origin.scheme, origin.netloc, path, origin.query, ""))


def browser_client_ip(scope: Scope) -> str:
    """Honor forwarded addresses only from configured authentication proxies."""
    return client_ip(scope)


async def app_session_parent_is_active(handle: str, user_id: UUID) -> bool:
    async with asyncio.timeout(5):
        information = await get_session_information(handle)
    if (
        information is None
        or information.user_id != str(user_id)
        or information.expiry <= int(time.time() * 1000)
    ):
        return False
    try:
        await _assert_local_user_can_authenticate(user_id)
    except ValueError:
        return False
    return True
