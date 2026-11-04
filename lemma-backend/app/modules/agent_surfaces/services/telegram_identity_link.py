"""A member proves which Telegram account is theirs, from a signed-in session.

Telegram gives a bot no email and, outside a contact a person chooses to share,
no phone, and its @username proves nothing -- anybody can take a free one. A
member with no verified number on their profile could not be recognised by a
pod's own bot at all: in a group they ran as a stranger, with Public reads
only. This is the other way round: the member, signed in to Lemma, asks for a
one-time code; opens it in Telegram, from their account, as ``/start lk_<code>``
in a private chat with the bot; and confirms, back in the same Lemma session,
the account it was opened from. Only then is the account theirs.

Both halves are needed, and that is the whole design. The code alone proves
nothing about who opened it -- a link is easily passed on, and whoever opened
a forwarded one would get the member's access in every chat with the bot. The
confirm alone proves nothing about the account. Together: the account that
opened the code is the one the member, in their own session, says is theirs,
seeing its name and username as they do.

From a group's people, the member taps "This is me" on somebody the pod took
for a stranger, and the code also names that person's Telegram id: opened from
any other account it is spent at once, and the member is told so. The bot's
reply to whoever opened it never names the member.

One use, ten minutes, kept in Redis like the group link's code
(``telegram_group_links``). Names are never matched on to bind anybody
(``identity_resolution_service``): a name that looks like a member's only
decides who is asked whether it is them.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from uuid import UUID

from redis.exceptions import RedisError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (  # noqa: E501
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.verified_surface_identity_repository import (  # noqa: E501
    VerifiedSurfaceIdentityRepository,
)
from app.modules.agent_surfaces.services.onboarding_transport import binding_key_of
from app.modules.agent_surfaces.services.proved_account_folds import (
    fold_proved_account,
)

logger = get_logger(__name__)

_TTL_SECONDS = 600
_CODE = "agent_surfaces:telegram_identity_link:"
_OPENED = "agent_surfaces:telegram_identity_link:opened:"

#: ``/start lk_<code>`` in a private chat with the bot. Telegram's start
#: parameter is 1-64 of ``[A-Za-z0-9_-]``, so the prefix and code fit in it.
_START = re.compile(r"^/start(?:@[A-Za-z0-9_]+)?\s+lk_([A-Za-z0-9_-]{16,60})\s*$")

#: What the bot says to whoever opened the code. None of it names the member.
OPENED_REPLY = "Got it. Go back to Lemma to finish linking this Telegram account."
EXPIRED_REPLY = "This link has expired or was already used. Start again from Lemma."
OTHER_ACCOUNT_REPLY = (
    "This link was made for a different Telegram account, so it no longer works."
)


class LinkUnavailable(RuntimeError):
    """No code could be kept, or the bot has no username to link to."""


class LinkState(StrEnum):
    #: Nobody has opened it in Telegram yet.
    WAITING = "WAITING"
    #: Opened from an account the member can now confirm or decline.
    OPENED = "OPENED"
    #: Opened from an account other than the one it was made for: spent.
    OTHER_ACCOUNT = "OTHER_ACCOUNT"
    #: Expired, spent, declined, or never the caller's.
    GONE = "GONE"


@dataclass(frozen=True, slots=True)
class TelegramAccount:
    """The account a code was opened from, as Telegram named it."""

    external_user_id: str
    name: str | None
    username: str | None


@dataclass(frozen=True, slots=True)
class LinkCode:
    """What a code was minted for."""

    user_id: UUID
    pod_id: UUID
    surface_id: UUID
    #: The Telegram id it must be opened from, when minted for somebody the
    #: pod took for a stranger; ``None`` from the member's profile.
    expected_external_user_id: str | None = None


@dataclass(frozen=True, slots=True)
class LinkStatus:
    state: LinkState
    account: TelegramAccount | None = None


def start_code(text: str | None) -> str | None:
    """The code in a private chat's ``/start lk_<code>``, if that is what it is."""
    match = _START.match((text or "").strip())
    return match.group(1) if match else None


async def mint_link(
    code: LinkCode, *, bot_username: str | None, redis=None
) -> tuple[str, str, datetime]:
    """``(code, url, expires_at)`` for the member to open in Telegram."""
    username = (bot_username or "").strip().lstrip("@")
    if not re.match(r"^[A-Za-z][A-Za-z0-9_]{3,31}$", username):
        raise LinkUnavailable("Telegram has not said what this bot is called")
    secret = secrets.token_urlsafe(24)
    record = {
        "user_id": str(code.user_id),
        "pod_id": str(code.pod_id),
        "surface_id": str(code.surface_id),
        "expected": code.expected_external_user_id,
    }
    client = redis or get_redis()
    try:
        await client.set(_CODE + secret, _encode(record), ex=_TTL_SECONDS)
    except (RedisError, OSError) as exc:
        raise LinkUnavailable("Could not keep the link's code") from exc
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=_TTL_SECONDS)
    return secret, f"https://t.me/{username}?start=lk_{secret}", expires_at


async def peek_link(secret: str, *, redis=None) -> LinkCode | None:
    """What a code was minted for, without spending it."""
    return await _code(redis or get_redis(), secret)


async def open_link(
    secret: str,
    *,
    parsed: ParsedInboundSurfaceEvent,
    received_by: list[UUID] | None,
    shared_bot_surface: bool | None,
    redis=None,
) -> str:
    """The bot's reply to ``/start lk_<code>`` from the account in ``parsed``.

    ``received_by`` is the surfaces the delivering bot serves, when it says;
    ``shared_bot_surface`` whether the code's surface is answered by the
    platform's shared bot (only then may a platform-wide delivery carry it).
    """
    client = redis or get_redis()
    code = await _code(client, secret)
    account = _account(parsed)
    if code is None or account is None:
        return EXPIRED_REPLY
    if received_by is not None and code.surface_id not in received_by:
        return EXPIRED_REPLY
    if received_by is None and not shared_bot_surface:
        return EXPIRED_REPLY
    if (
        code.expected_external_user_id is not None
        and code.expected_external_user_id != account.external_user_id
    ):
        await _keep_opened(client, secret, account, other_account=True)
        await _forget(client, _CODE + secret)
        logger.info("agent_surfaces.telegram_identity_link.other_account.observed")
        return OTHER_ACCOUNT_REPLY
    if not await _keep_opened(client, secret, account, other_account=False):
        return EXPIRED_REPLY
    return OPENED_REPLY


async def link_status(secret: str, *, user_id: UUID, redis=None) -> LinkStatus:
    """Where the member's code stands, for their session to show."""
    client = redis or get_redis()
    code = await _code(client, secret)
    opened = await _opened(client, secret)
    if opened is not None and opened.get("other_account"):
        # The code is spent; only its owner may hear that it was misused.
        owner = opened.get("user_id")
        if owner == str(user_id):
            return LinkStatus(LinkState.OTHER_ACCOUNT)
        return LinkStatus(LinkState.GONE)
    if code is None or code.user_id != user_id:
        return LinkStatus(LinkState.GONE)
    if opened is None:
        return LinkStatus(LinkState.WAITING)
    return LinkStatus(LinkState.OPENED, account=_opened_account(opened))


async def take_confirmed(
    secret: str, *, user_id: UUID, redis=None
) -> tuple[LinkCode, TelegramAccount] | None:
    """Spend the member's code and say what it was opened from, when it is
    theirs to confirm and was opened from the account it was made for.

    Redis only, so a caller holding a database connection releases it first;
    ``bind_confirmed`` then writes the binding.
    """
    client = redis or get_redis()
    code = await _code(client, secret)
    opened = await _opened(client, secret)
    if (
        code is None
        or code.user_id != user_id
        or opened is None
        or opened.get("other_account")
    ):
        return None
    await _forget(client, _CODE + secret, _OPENED + secret)
    return code, _opened_account(opened)


async def bind_confirmed(
    uow: SqlAlchemyUnitOfWork, code: LinkCode, account: TelegramAccount
) -> bool:
    """Bind the account to the member who confirmed it.

    ``False`` when the account is already somebody else's: proving it again is
    not how it changes hands.
    """
    identity = await VerifiedSurfaceIdentityRepository(uow).prove_by_session(
        binding_key=binding_key_of(
            SurfacePlatform.TELEGRAM.value,
            None,
            installation_id=None,
            actor=account.external_user_id,
        ),
        platform=SurfacePlatform.TELEGRAM.value,
        external_user_id=account.external_user_id,
        user_id=code.user_id,
        surface_id=code.surface_id,
    )
    if identity is None:
        return False
    # The group log and the resolver read the cache first; a member just
    # proved is a member on their very next line.
    await ExternalSurfaceUserRepository(uow).upsert(
        platform=SurfacePlatform.TELEGRAM.value,
        tenant_id=None,
        external_user_id=account.external_user_id,
        email=None,
        phone=None,
        display_name=account.name,
        raw_profile=None,
        resolved_user_id=code.user_id,
    )
    # The contacts the pods made for the account, where the member is one, are
    # them: their lines in groups become the member's.
    await fold_proved_account(
        uow, user_id=code.user_id, external_user_id=account.external_user_id
    )
    logger.info(
        "agent_surfaces.telegram_identity_link.proved.observed",
        surface_id=str(code.surface_id),
    )
    return True


async def decline_link(secret: str, *, user_id: UUID, redis=None) -> None:
    """The member says the account that opened it is not theirs: spend it."""
    client = redis or get_redis()
    code = await _code(client, secret)
    if code is not None and code.user_id == user_id:
        await _forget(client, _CODE + secret, _OPENED + secret)


def is_shared_bot_surface(surface: AgentSurfaceEntity) -> bool:
    """Whether the platform's shared bot answers for this surface, rather than
    a bot of the pod's own, whose deliveries name the surface they are for."""
    return surface.pod_connector_slug is None


def _account(parsed: ParsedInboundSurfaceEvent) -> TelegramAccount | None:
    if not parsed.is_dm or not parsed.sender_external_user_id:
        return None
    username = parsed.metadata.get("sender_username")
    return TelegramAccount(
        external_user_id=parsed.sender_external_user_id,
        name=parsed.sender_display_name,
        username=username if isinstance(username, str) and username else None,
    )


async def _code(client, secret: str) -> LinkCode | None:
    raw = await _get(client, _CODE + secret)
    if not isinstance(raw, dict):
        return None
    try:
        return LinkCode(
            user_id=UUID(str(raw["user_id"])),
            pod_id=UUID(str(raw["pod_id"])),
            surface_id=UUID(str(raw["surface_id"])),
            expected_external_user_id=(
                str(raw["expected"]) if raw.get("expected") else None
            ),
        )
    except KeyError, ValueError:
        return None


async def _opened(client, secret: str) -> dict[str, object] | None:
    raw = await _get(client, _OPENED + secret)
    return raw if isinstance(raw, dict) else None


def _opened_account(opened: dict[str, object]) -> TelegramAccount:
    name, username = opened.get("name"), opened.get("username")
    return TelegramAccount(
        external_user_id=str(opened.get("external_user_id") or ""),
        name=name if isinstance(name, str) else None,
        username=username if isinstance(username, str) else None,
    )


async def _keep_opened(
    client, secret: str, account: TelegramAccount, *, other_account: bool
) -> bool:
    """Record who opened the code; ``False`` when somebody already had."""
    code = await _code(client, secret)
    record = {
        "external_user_id": account.external_user_id,
        "name": account.name,
        "username": account.username,
        "other_account": other_account,
        "user_id": str(code.user_id) if code is not None else None,
    }
    try:
        kept = await client.set(
            _OPENED + secret, _encode(record), ex=_TTL_SECONDS, nx=True
        )
    except RedisError, OSError:
        logger.warning(
            "agent_surfaces.telegram_identity_link.store_unavailable.degraded",
            exc_info=True,
        )
        return False
    return bool(kept)


async def _get(client, key: str) -> object:
    try:
        raw = await client.get(key)
    except RedisError, OSError:
        logger.warning(
            "agent_surfaces.telegram_identity_link.store_unavailable.degraded",
            exc_info=True,
        )
        return None
    return _decode(raw) if raw else None


# A record is a handful of short fields; encoding one is no work for the loop.
def _encode(record: dict[str, object]) -> str:
    return json.dumps(record)


def _decode(raw: object) -> object:
    try:
        return json.loads(raw.decode() if isinstance(raw, bytes) else str(raw))
    except ValueError:
        return None


async def _forget(client, *keys: str) -> None:
    try:
        await client.delete(*keys)
    except RedisError, OSError:
        logger.warning(
            "agent_surfaces.telegram_identity_link.store_unavailable.degraded",
            exc_info=True,
        )
