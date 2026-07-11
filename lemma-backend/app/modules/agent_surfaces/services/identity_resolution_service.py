from __future__ import annotations

import re
from uuid import UUID

from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.models import SurfaceSenderProfile
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (
    ExternalSurfaceUserRepository,
)
from app.composition.surface_identity import UserRepository


class SurfaceIdentityResolutionService:
    """Resolve an inbound platform message sender to an internal Lemma user.

    Resolution order
    ----------------
    1. Cache hit — ExternalSurfaceUser row already has a resolved_user_id.
    2. Telegram username — a Telegram sender whose @username matches a user's
       ``telegram_username`` resolves directly (no contact-share needed).
    3. Email match — profile email (fetched from platform API) matched against
       the users table.
    4. Phone match — fallback for platforms that expose phone numbers (e.g. the
       Telegram contact-share flow).

    No connected-account (OAuth Account table) lookups are performed.  Platform
    adapters fetch sender emails directly from the platform API (Teams Graph /
    Slack API), so org members never need to individually connect their accounts.
    If no match is found the caller sends a signup link.
    """

    def __init__(self, uow, external_user_repository: ExternalSurfaceUserRepository):
        self.uow = uow
        self._users = UserRepository(uow)
        self.external_user_repository = external_user_repository

    async def resolve(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
        sender_profile: SurfaceSenderProfile | None = None,
    ) -> ResolvedSurfaceUser:
        profile = sender_profile or SurfaceSenderProfile()
        external_user_id = (
            str(profile.external_user_id or event.sender_external_user_id or "")
            or None
        )
        email = profile.email or event.sender_email
        phone = _normalize_phone_number(profile.phone or event.sender_phone)
        display_name = profile.display_name or event.sender_display_name

        # ── 1. Upsert the ExternalSurfaceUser row with whatever we know ─────
        external_user = None
        if external_user_id:
            external_user = await self.external_user_repository.upsert(
                platform=event.platform,
                tenant_id=event.tenant_id,
                external_user_id=external_user_id,
                email=email,
                phone=phone,
                display_name=display_name,
                raw_profile=profile.raw_profile or profile.model_dump(exclude_none=True) or event.raw_payload,
            )
            # Cache hit — previously resolved, skip DB lookup.
            if external_user.resolved_user_id:
                return ResolvedSurfaceUser(
                    internal_user_id=external_user.resolved_user_id,
                    external_user_id=external_user.external_user_id,
                    email=external_user.email,
                    phone=external_user.phone,
                    display_name=external_user.display_name,
                )

        # ── 2-4. Match against Lemma users: telegram username, then email,
        #         then phone ─────────────────────────────────────────────────
        user_id = await self._match_user(
            email=email,
            phone=phone,
            telegram_username=_telegram_username(event),
        )

        # Persist the resolved_user_id so the next message is a cache hit.
        if external_user_id:
            external_user = await self.external_user_repository.upsert(
                platform=event.platform,
                tenant_id=event.tenant_id,
                external_user_id=external_user_id,
                email=email,
                phone=phone,
                display_name=display_name,
                raw_profile=profile.raw_profile or profile.model_dump(exclude_none=True) or event.raw_payload,
                resolved_user_id=user_id,
            )

        return ResolvedSurfaceUser(
            internal_user_id=user_id,
            external_user_id=external_user_id,
            email=email,
            phone=phone,
            display_name=display_name or (external_user.display_name if external_user else None),
        )

    async def _match_user(
        self,
        *,
        email: str | None,
        phone: str | None,
        telegram_username: str | None = None,
    ) -> UUID | None:
        """Return the internal user_id for this sender, or None if not found."""
        # Telegram username first: a direct @username match links the sender
        # without the contact-share (phone) flow.
        if telegram_username:
            user_id = await self._match_user_by_telegram_username(telegram_username)
            if user_id:
                return user_id

        if email:
            user_id = await self._users.get_id_by_email_insensitive(email)
            if user_id:
                return user_id

        if phone:
            user_id = await self._match_user_by_phone(phone)
            if user_id:
                return user_id

        return None

    async def _match_user_by_telegram_username(self, username: str) -> UUID | None:
        cleaned = str(username or "").strip().lstrip("@").lower()
        if not cleaned:
            return None
        return await self._users.get_id_by_telegram_lower(cleaned)

    async def _match_user_by_phone(self, phone: str) -> UUID | None:
        candidates = _phone_lookup_candidates(phone)
        if not candidates:
            return None
        # Only a unique phone match is trusted (avoid linking shared numbers).
        ids = await self._users.get_ids_by_mobile_numbers(candidates)
        return ids[0] if len(ids) == 1 else None


def _telegram_username(event: ParsedInboundSurfaceEvent) -> str | None:
    """The Telegram sender's @username (only Telegram populates it), else None."""
    if event.platform != SurfacePlatform.TELEGRAM:
        return None
    metadata = event.metadata if isinstance(event.metadata, dict) else {}
    username = metadata.get("sender_username")
    return str(username).strip() or None if username else None


def _normalize_phone_number(phone: str | None) -> str | None:
    raw = str(phone or "").strip()
    if not raw:
        return None
    has_plus = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    return f"+{digits}" if has_plus else digits


def _phone_lookup_candidates(phone: str) -> list[str]:
    normalized = _normalize_phone_number(phone)
    if not normalized:
        return []

    digits_only = re.sub(r"\D", "", normalized)
    candidates: list[str] = []
    for candidate in (
        normalized,
        digits_only,
        f"+{digits_only}" if digits_only else None,
    ):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates
