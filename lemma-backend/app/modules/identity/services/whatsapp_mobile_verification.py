"""Short-lived WhatsApp mobile-verification transactions.

The user sends a reserved message to Lemma's global WhatsApp number. Lemma
never sends an OTP, and the sender number in Meta's signed webhook is the proof
that the user controls the submitted number.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import quote
from uuid import UUID

import httpx
from redis.asyncio import Redis

from app.core.infrastructure.redis.client import get_redis
from sqlalchemy.exc import IntegrityError

from app.modules.agent_surfaces.contracts.whatsapp import (
    GlobalWhatsAppDeliveryError,
    global_whatsapp_configuration,
    send_global_whatsapp_text,
)
from app.core.config import settings
from app.core.helpers.identifiers import normalize_mobile_e164
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.events.publisher import EventPublisher
from app.core.log.log import get_logger
from app.modules.identity.domain.events import UserMobileChangedEvent
from app.modules.identity.infrastructure.mobile_number_claims import (
    acquire_mobile_number_claim_lock,
    get_other_mobile_number_owner_id,
)
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.infrastructure.user_cache import get_user_cache
from app.modules.identity.config import identity_settings

logger = get_logger(__name__)

WhatsAppVerificationStatus = Literal["PENDING", "VERIFIED", "EXPIRED"]

_PREFIX = "identity:mobile-verification:whatsapp"
_TTL_SECONDS = 10 * 60
_STATUS_TTL_SECONDS = 2 * 60
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_MESSAGE_PREFIX = "LEMMA VERIFY "
_META_GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
_SUCCESS_FEEDBACK = (
    "Your mobile number is verified for Lemma. You can return to Lemma now."
)
_FAILURE_FEEDBACK = (
    "We could not verify this mobile number. Return to Lemma, request a new code, "
    "and send the full message from the mobile number on your profile."
)

WhatsAppFeedbackSender = Callable[..., Awaitable[bool]]

_CLAIM_LUA = """
local transaction_id = redis.call('GET', KEYS[1])
if not transaction_id then return {'missing'} end
local transaction_key = ARGV[1] .. transaction_id
if redis.call('EXISTS', transaction_key) == 0 then return {'missing'} end
local expected_phone = redis.call('HGET', transaction_key, 'phone')
local user_id = redis.call('HGET', transaction_key, 'user_id')
-- An empty 'phone' is a transaction that never declared one. There is nothing
-- to match the sender against, because the sender *is* the answer: Meta signed
-- the webhook that carried their wa_id, so the number arrives proven either way.
if expected_phone ~= '' and expected_phone ~= ARGV[2] then return {'sender_mismatch'} end
local status = redis.call('HGET', transaction_key, 'status')
local processing_message = redis.call('HGET', transaction_key, 'message_id')
if status == 'verified' then
  if processing_message == ARGV[6] then return {'verified'} end
  return {'replayed'}
end
if redis.call('GET', ARGV[3] .. user_id) ~= transaction_id then return {'superseded'} end
-- The per-number reservation only exists when a number was declared, so an
-- undeclared transaction has no key here to be superseded by.
if expected_phone ~= '' and redis.call('GET', ARGV[4] .. ARGV[5]) ~= transaction_id then return {'superseded'} end
if status == 'processing' and processing_message ~= ARGV[6] then return {'replayed'} end
redis.call('HSET', transaction_key, 'status', 'processing', 'message_id', ARGV[6])
return {'claimed', transaction_id, user_id, expected_phone}
"""

_COMPLETE_LUA = """
if redis.call('EXISTS', KEYS[1]) == 0 then return {'missing'} end
local user_id = redis.call('HGET', KEYS[1], 'user_id')
local phone_hash = redis.call('HGET', KEYS[1], 'phone_hash')
redis.call('HSET', KEYS[1], 'status', 'verified')
redis.call('EXPIRE', KEYS[1], ARGV[3])
redis.call('DEL', ARGV[1] .. user_id)
if phone_hash ~= '' then redis.call('DEL', ARGV[2] .. phone_hash) end
return {'verified'}
"""

_RATE_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {count, redis.call('TTL', KEYS[1])}
"""


class WhatsAppVerificationError(ValueError):
    pass


class WhatsAppVerificationUnavailable(WhatsAppVerificationError):
    pass


class WhatsAppVerificationRateLimited(WhatsAppVerificationError):
    def __init__(self, retry_after_seconds: int):
        super().__init__("Too many mobile verification attempts")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class WhatsAppVerificationTransaction:
    transaction_id: str
    code: str
    whatsapp_url: str
    display_number: str
    expires_at: datetime


@dataclass(frozen=True)
class WhatsAppVerificationConfig:
    available: bool
    display_number: str | None = None


@dataclass(frozen=True)
class _ClaimedVerification:
    transaction_id: str
    user_id: UUID


def is_whatsapp_verification_configured() -> bool:
    whatsapp = global_whatsapp_configuration()
    return bool(
        identity_settings.auth_whatsapp_mobile_verification_enabled
        and whatsapp.access_token
        and whatsapp.phone_number_id
        and whatsapp.app_secret
        and whatsapp.verify_token
        and whatsapp.webhook_security_enabled
    )


def parse_reserved_verification_message(
    payload: dict,
) -> tuple[str, str, str, str] | None:
    """Return code, sender wa_id, destination id and message id for our command."""
    try:
        value = payload["entry"][0]["changes"][0]["value"]
        message = value["messages"][0]
        destination = str(value["metadata"]["phone_number_id"])
        if message.get("type") != "text":
            return None
        body = str((message.get("text") or {}).get("body") or "")
        if not body.startswith(_MESSAGE_PREFIX):
            return None
        code = body.removeprefix(_MESSAGE_PREFIX)
        if len(code) > 64:
            code = ""
        sender = str(message.get("from") or "")
        message_id = str(message.get("id") or "")
        if not sender or not destination or not message_id:
            return None
        return code, sender, destination, message_id
    except KeyError, IndexError, TypeError:
        return None


class WhatsAppMobileVerificationService:
    def __init__(
        self,
        redis_url: str | None = None,
        *,
        ttl_seconds: int = _TTL_SECONDS,
        feedback_sender: WhatsAppFeedbackSender | None = None,
    ):
        self._redis_url = redis_url or settings.redis_url
        self._ttl_seconds = ttl_seconds
        self._redis: Redis | None = None
        self._lock = asyncio.Lock()
        self._display_number: str | None = None
        self._feedback_sender = feedback_sender or send_global_whatsapp_text

    async def _send_feedback(
        self,
        *,
        sender_wa_id: str,
        whatsapp_message_id: str,
        succeeded: bool,
    ) -> None:
        try:
            sent = await self._feedback_sender(
                to=sender_wa_id,
                body=_SUCCESS_FEEDBACK if succeeded else _FAILURE_FEEDBACK,
                reply_to_message_id=whatsapp_message_id,
            )
        except GlobalWhatsAppDeliveryError as exc:
            logger.warning(
                "identity.mobile_verification.whatsapp.feedback_send_failed",
                outcome="success" if succeeded else "failure",
                error_type=type(exc).__name__,
            )
            return
        if not sent:
            logger.warning(
                "identity.mobile_verification.whatsapp.feedback_send_failed",
                outcome="success" if succeeded else "failure",
                error_type="Unavailable",
            )

    async def _get_redis(self) -> Redis:
        if self._redis is not None:
            return self._redis
        async with self._lock:
            if self._redis is None:
                self._redis = get_redis(url=self._redis_url)
        return self._redis

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _transaction_key(transaction_id: str) -> str:
        return f"{_PREFIX}:transaction:{transaction_id}"

    @staticmethod
    def _code_key(code: str) -> str:
        return f"{_PREFIX}:code:{WhatsAppMobileVerificationService._digest(code)}"

    @staticmethod
    def _user_key(user_id: UUID | str) -> str:
        return f"{_PREFIX}:user:{user_id}"

    @staticmethod
    def _phone_key(phone_hash: str) -> str:
        return f"{_PREFIX}:phone:{phone_hash}"

    async def config(self) -> WhatsAppVerificationConfig:
        if not is_whatsapp_verification_configured():
            return WhatsAppVerificationConfig(available=False)
        if self._display_number is None:
            configured = str(
                global_whatsapp_configuration().display_phone_number or ""
            ).strip()
            if configured:
                self._display_number = configured
            else:
                self._display_number = await self._lookup_display_number()
        return WhatsAppVerificationConfig(
            available=bool(self._display_number), display_number=self._display_number
        )

    async def _lookup_display_number(self) -> str | None:
        whatsapp = global_whatsapp_configuration()
        access_token = whatsapp.access_token
        phone_number_id = whatsapp.phone_number_id
        if not access_token or not phone_number_id:
            return None
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{_META_GRAPH_API_BASE}/{phone_number_id}",
                    params={"fields": "display_phone_number"},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                response.raise_for_status()
            value = str(response.json().get("display_phone_number") or "").strip()
            return value or None
        except httpx.HTTPError, TypeError, ValueError:
            logger.info("identity.mobile_verification.whatsapp.number_lookup_failed")
            return None

    async def _enforce_start_limit(self, client_key: str) -> None:
        redis = await self._get_redis()
        result = await redis.eval(
            _RATE_LUA,
            1,
            f"{_PREFIX}:rate:start:{self._digest(client_key)}",
            15 * 60,
        )
        count = int(result[0]) if result else 1
        if count > 5:
            retry_after = max(1, int(result[1]) if len(result) > 1 else 1)
            raise WhatsAppVerificationRateLimited(retry_after)

    async def start(
        self, *, user_id: UUID, mobile_number: str | None = None, client_key: str
    ) -> WhatsAppVerificationTransaction:
        """Mint a code, with or without a number to hold it to.

        Declaring the number is the older half of this. It buys one thing: a
        leaked code cannot bind somebody else's phone to this account, because
        the claim is refused unless the sender matches what was declared. Where
        the number is already known -- profile settings, where someone typed it
        into the form -- that check is free, so it stays.

        Where it is not known, asking for it is the whole friction: Telegram
        never asks, because its OIDC claim carries a verified `phone_number`
        (`telegram_oidc.verify_mobile`), and WhatsApp is no different in what it
        knows -- Meta signs the webhook, and the sender's wa_id is in it. So an
        undeclared transaction binds whichever number sends the code, which is
        the same number the declared flow would have written anyway.
        """
        config = await self.config()
        if not config.available or not config.display_number:
            raise WhatsAppVerificationUnavailable(
                "WhatsApp mobile verification is not configured"
            )
        phone = normalize_mobile_e164(mobile_number) if mobile_number else ""
        await self._enforce_start_limit(f"{user_id}:{client_key}")

        phone_hash = self._digest(phone) if phone else ""
        transaction_id = secrets.token_urlsafe(24)
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(10))
        message = f"{_MESSAGE_PREFIX}{code}"
        display_digits = "".join(c for c in config.display_number if c.isdigit())
        if not display_digits:
            raise WhatsAppVerificationUnavailable(
                "The global WhatsApp display number is unavailable"
            )
        redis = await self._get_redis()
        key = self._transaction_key(transaction_id)
        async with redis.pipeline(transaction=True) as pipe:
            pipe.hset(
                key,
                mapping={
                    "user_id": str(user_id),
                    "phone": phone,
                    "phone_hash": phone_hash,
                    "status": "pending",
                },
            )
            pipe.expire(key, self._ttl_seconds)
            pipe.set(self._code_key(code), transaction_id, ex=self._ttl_seconds)
            pipe.set(self._user_key(user_id), transaction_id, ex=self._ttl_seconds)
            if phone_hash:
                pipe.set(
                    self._phone_key(phone_hash), transaction_id, ex=self._ttl_seconds
                )
            await pipe.execute()

        logger.info(
            "identity.mobile_verification.whatsapp.started", user_id=str(user_id)
        )
        return WhatsAppVerificationTransaction(
            transaction_id=transaction_id,
            code=code,
            whatsapp_url=f"https://wa.me/{display_digits}?text={quote(message)}",
            display_number=config.display_number,
            expires_at=datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() + self._ttl_seconds,
                tz=timezone.utc,
            ),
        )

    async def status(
        self, *, transaction_id: str, user_id: UUID
    ) -> WhatsAppVerificationStatus:
        redis = await self._get_redis()
        fields = await redis.hgetall(self._transaction_key(transaction_id))
        if not fields or fields.get("user_id") != str(user_id):
            return "EXPIRED"
        status = fields.get("status")
        if status == "verified":
            return "VERIFIED"
        if await redis.get(self._user_key(user_id)) != transaction_id:
            return "EXPIRED"
        return "PENDING"

    async def _claim_message(
        self,
        *,
        redis: Redis,
        code: str,
        sender_phone: str,
        whatsapp_message_id: str,
    ) -> tuple[str, _ClaimedVerification | None]:
        phone_hash = self._digest(sender_phone)
        result = await redis.eval(
            _CLAIM_LUA,
            1,
            self._code_key(code),
            f"{_PREFIX}:transaction:",
            sender_phone,
            f"{_PREFIX}:user:",
            f"{_PREFIX}:phone:",
            phone_hash,
            whatsapp_message_id,
        )
        state = str(result[0]) if result else "missing"
        if state != "claimed":
            return state, None
        return state, _ClaimedVerification(
            transaction_id=str(result[1]),
            user_id=UUID(str(result[2])),
        )

    async def _persist_claim(
        self, *, claim: _ClaimedVerification, sender_phone: str
    ) -> bool:
        async with async_session_maker() as session:
            await acquire_mobile_number_claim_lock(
                session, sender_phone.removeprefix("+")
            )
            user = await session.get(User, claim.user_id)
            if (
                user is None
                or not user.is_active
                or user.is_deleted
                or not user.is_verified
            ):
                logger.info("identity.mobile_verification.whatsapp.ineligible_user")
                return False
            owner = await get_other_mobile_number_owner_id(
                session,
                digits=sender_phone.removeprefix("+"),
                user_id=claim.user_id,
            )
            if owner is not None:
                logger.info("identity.mobile_verification.whatsapp.owner_conflict")
                return False
            user.mobile_number = sender_phone
            user.mobile_verified_at = datetime.now(timezone.utc)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                logger.info("identity.mobile_verification.whatsapp.owner_conflict")
                return False
        return True

    async def _complete_claim(
        self, *, redis: Redis, claim: _ClaimedVerification
    ) -> None:
        await get_user_cache().invalidate(claim.user_id)
        phone_changed = UserMobileChangedEvent(user_id=claim.user_id)
        await EventPublisher.publish(phone_changed.stream_name(), phone_changed)
        await redis.eval(
            _COMPLETE_LUA,
            1,
            self._transaction_key(claim.transaction_id),
            f"{_PREFIX}:user:",
            f"{_PREFIX}:phone:",
            _STATUS_TTL_SECONDS,
        )
        logger.info(
            "identity.mobile_verification.whatsapp.succeeded",
            user_id=str(claim.user_id),
        )

    async def consume_message(
        self,
        *,
        code: str,
        sender_wa_id: str,
        destination_phone_number_id: str,
        whatsapp_message_id: str,
    ) -> bool:
        whatsapp = global_whatsapp_configuration()
        if (
            not is_whatsapp_verification_configured()
            or destination_phone_number_id != whatsapp.phone_number_id
        ):
            return False
        try:
            sender_phone = normalize_mobile_e164(f"+{sender_wa_id.lstrip('+')}")
        except ValueError:
            logger.info("identity.mobile_verification.whatsapp.invalid_sender")
            return False

        if len(code) != 10 or any(
            character not in _CODE_ALPHABET for character in code
        ):
            logger.info(
                "identity.mobile_verification.whatsapp.rejected",
                reason="invalid_format",
            )
            await self._send_feedback(
                sender_wa_id=sender_wa_id,
                whatsapp_message_id=whatsapp_message_id,
                succeeded=False,
            )
            return False

        redis = await self._get_redis()
        state, claim = await self._claim_message(
            redis=redis,
            code=code,
            sender_phone=sender_phone,
            whatsapp_message_id=whatsapp_message_id,
        )
        if state == "verified":
            await self._send_feedback(
                sender_wa_id=sender_wa_id,
                whatsapp_message_id=whatsapp_message_id,
                succeeded=True,
            )
            return True
        if claim is None:
            logger.info(
                "identity.mobile_verification.whatsapp.rejected",
                reason=state,
            )
            await self._send_feedback(
                sender_wa_id=sender_wa_id,
                whatsapp_message_id=whatsapp_message_id,
                succeeded=False,
            )
            return False

        if not await self._persist_claim(claim=claim, sender_phone=sender_phone):
            await self._send_feedback(
                sender_wa_id=sender_wa_id,
                whatsapp_message_id=whatsapp_message_id,
                succeeded=False,
            )
            return False

        await self._complete_claim(redis=redis, claim=claim)
        await self._send_feedback(
            sender_wa_id=sender_wa_id,
            whatsapp_message_id=whatsapp_message_id,
            succeeded=True,
        )
        return True

    async def close(self) -> None:
        # The client is shared process-wide; closing it here would break
        # every other component still using the same pool. Disposal is
        # close_redis_clients()'s job at lifespan shutdown.
        self._redis = None


_service: WhatsAppMobileVerificationService | None = None


def get_whatsapp_mobile_verification_service() -> WhatsAppMobileVerificationService:
    global _service
    if _service is None or _service._redis_url != settings.redis_url:
        _service = WhatsAppMobileVerificationService()
    return _service


async def close_whatsapp_mobile_verification_service() -> None:
    global _service
    if _service is None:
        return
    current = _service
    _service = None
    await current.close()
