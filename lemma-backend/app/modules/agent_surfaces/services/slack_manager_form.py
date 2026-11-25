"""What was typed into the manager's "Add to Slack" modal, kept apart from its work item.

The modal takes a configuration token's refresh token, or a hand-made app's
client secret and signing secret. Making the app takes longer than Slack's
three seconds, so the work is queued -- and a work item's payload is plaintext
JSON in Postgres. So the values never go in it: the request seals them with
the vault, bound to the Redis key they are kept under, keeps them there for ten
minutes, and the item carries only the reference this returns. The worker takes
them once.

Without Redis there is nowhere to keep them, and the modal says to try again;
nothing is queued with a secret in it either way.
"""

from __future__ import annotations

from secrets import token_urlsafe

from pydantic import SecretStr
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.slack_manager import AddForm
from app.modules.vault.contracts import (
    SealedValueInvalid,
    VaultUnavailable,
    open_json,
    seal_value,
)

logger = get_logger(__name__)

#: Longer than a queued item waits, short enough that a lost one is soon gone.
FORM_TTL_SECONDS = 10 * 60

_cache: RedisJsonCache[str] | None = None


def _forms() -> RedisJsonCache[str]:
    global _cache
    if _cache is None:
        _cache = RedisJsonCache(
            redis_url=settings.redis_url,
            # v2: sealed by the vault. A v1 entry (the Fernet cipher's) is
            # simply not found, and the modal says to try again.
            key_prefix="surfaces:slack-manager-form:v2",
            ttl_seconds=FORM_TTL_SECONDS,
        )
    return _cache


#: What a kept form is sealed for; bound with its reference too, so a sealed
#: value moved to another key does not open.
_SEALED_PURPOSE = "agent_surfaces.slack_manager.add_form"


async def keep_form(form: AddForm) -> str | None:
    """Seal the values and keep them; the reference, or None without Redis."""
    reference = token_urlsafe(24)
    sealed = await seal_value(
        {
            "refresh_token": form.refresh_token.get_secret_value(),
            "client_id": form.client_id,
            "client_secret": form.client_secret.get_secret_value(),
            "signing_secret": form.signing_secret.get_secret_value(),
        },
        purpose=_SEALED_PURPOSE,
        bindings=(reference,),
    )
    try:
        await _forms().set_raw(reference, sealed)
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
        values = await open_json(raw, purpose=_SEALED_PURPOSE, bindings=(reference,))
    except SealedValueInvalid, VaultUnavailable:
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
