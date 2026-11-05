"""What was typed into the manager's "Add to Slack" modal, kept apart from its work item.

The modal takes a configuration token's refresh token, or a hand-made app's
client secret and signing secret. Making the app takes longer than Slack's
three seconds, so the work is queued -- and a work item's payload is plaintext
JSON in Postgres. So the values never go in it: the request encrypts them with
the secret cipher and keeps the ciphertext in Redis for ten minutes, and the
item carries only the reference this returns. The worker takes them once.

Without Redis there is nowhere to keep them, and the modal says to try again;
nothing is queued with a secret in it either way.
"""

from __future__ import annotations

import json
from secrets import token_urlsafe

from pydantic import SecretStr
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.crypto import get_secret_cipher
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.slack_manager import AddForm

logger = get_logger(__name__)

#: Longer than a queued item waits, short enough that a lost one is soon gone.
FORM_TTL_SECONDS = 10 * 60

_cache: RedisJsonCache[str] | None = None


def _forms() -> RedisJsonCache[str]:
    global _cache
    if _cache is None:
        _cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="surfaces:slack-manager-form",
            ttl_seconds=FORM_TTL_SECONDS,
        )
    return _cache


def _encoded(sealed: dict[str, object] | None) -> str:
    """One envelope around four short form values: bounded, so on the loop."""
    return json.dumps(sealed)


def _decoded(raw: str) -> dict[str, object]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("not a kept form")
    return value


async def keep_form(form: AddForm) -> str | None:
    """Encrypt the values and keep them; the reference, or None without Redis."""
    sealed = await get_secret_cipher().encrypt_json_async(
        {
            "refresh_token": form.refresh_token.get_secret_value(),
            "client_id": form.client_id,
            "client_secret": form.client_secret.get_secret_value(),
            "signing_secret": form.signing_secret.get_secret_value(),
        }
    )
    reference = token_urlsafe(24)
    try:
        await _forms().set_raw(reference, _encoded(sealed))
    except RedisError:
        logger.warning("agent_surfaces.slack_manager.form_kept.degraded", exc_info=True)
        return None
    return reference


async def take_form(reference: str) -> AddForm | None:
    """The values a reference names, once: None when expired, taken or unreadable."""
    try:
        raw = await _forms().get_raw(reference)
        if raw is not None:
            await _forms().delete(reference)
    except RedisError:
        logger.warning(
            "agent_surfaces.slack_manager.form_taken.degraded", exc_info=True
        )
        return None
    if raw is None:
        return None
    try:
        values = await get_secret_cipher().decrypt_json_async(_decoded(raw)) or {}
    except ValueError:
        logger.warning(
            "agent_surfaces.slack_manager.form_unreadable.degraded", exc_info=True
        )
        return None
    return AddForm(
        refresh_token=SecretStr(str(values.get("refresh_token") or "")),
        client_id=str(values.get("client_id") or ""),
        client_secret=SecretStr(str(values.get("client_secret") or "")),
        signing_secret=SecretStr(str(values.get("signing_secret") or "")),
    )
