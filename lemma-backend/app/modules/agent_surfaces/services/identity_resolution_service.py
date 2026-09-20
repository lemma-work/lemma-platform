from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
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
from app.modules.agent_surfaces.domain.ports import SurfaceUserDirectoryPort
from app.modules.agent_surfaces.infrastructure.adapters.user_directory_adapter import (
    IdentityUserDirectoryAdapter,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class _UserMatch:
    user_id: UUID | None
    cacheable: bool = True


@dataclass(frozen=True, slots=True)
class _KnownSender:
    """What the platform said about this person, before a Lemma user is matched.

    Both the profile the adapter fetched and the event itself carry these, in
    that order of trust, and the same five reads were being spelled out at each
    of the two upsert sites.
    """

    external_user_id: str | None
    email: str | None
    phone: str | None
    display_name: str | None
    raw_profile: Any

    @classmethod
    def of(
        cls,
        profile: SurfaceSenderProfile,
        event: ParsedInboundSurfaceEvent,
    ) -> "_KnownSender":
        return cls(
            external_user_id=str(
                profile.external_user_id or event.sender_external_user_id or ""
            )
            or None,
            email=profile.email or event.sender_email,
            phone=_normalize_phone_number(profile.phone or event.sender_phone),
            display_name=profile.display_name or event.sender_display_name,
            raw_profile=profile.raw_profile
            or profile.model_dump(exclude_none=True)
            or event.raw_payload,
        )


def _email_sender_is_believable(event: ParsedInboundSurfaceEvent) -> bool:
    """May this inbound email's ``From:`` be resolved to a Lemma user?

    Only email is asked. Every chat platform asserts its sender inside a payload
    whose signature was already verified, so there is nothing here to doubt --
    ``sender_authentication`` is None for them and this passes.

    **There is no setting.** There used to be one, defaulting to "believe an
    unauthenticated ``From:``", on the reasoning that not every provider writes
    ``Authentication-Results`` and refusing blind would stop resolving every
    inbound sender. That reasoning rested on a question nobody had asked the
    provider: 20 of 20 real inbound messages carry the header, written by
    ``amazonses.com``, because Resend receives through SES. So the config was
    protecting against something that does not happen, at the price of leaving
    account takeover a single environment variable away -- and a flag whose safe
    value is the default is not a flag, it is an unexploited hole.

    What is left is the rule ``PS-SURF-022`` already stated and the code did not
    keep: a sender the receiving mail service did not vouch for does not become
    a member. They are a stranger, and the unresolved-sender path tells them how
    to get access -- which is what should happen to someone we cannot identify.

    ``None`` on an email surface is UNKNOWN, not "nothing to check". Only
    ``merge_received_email`` ever sets the verdict, so it is absent whenever
    enrichment did not run: no ``email_id`` on the webhook, an ``HTTPError`` on
    the body fetch, or the whole polling receiver. Reading that as the chat
    platforms' "no question to ask" meant an attacker who could make the fetch
    fail -- or a deployment in polling mode -- skipped this check entirely.
    """
    from app.modules.agent_surfaces.platforms.email_authentication import (
        EmailAuthenticationVerdict,
    )

    verdict = event.sender_authentication
    if verdict is None and not event.platform.is_email:
        return True
    if verdict == EmailAuthenticationVerdict.PASS:
        return True
    if verdict == EmailAuthenticationVerdict.FAIL:
        logger.warning(
            "agent_surfaces.identity.email_sender_failed_authentication.degraded",
            platform=str(event.platform),
            sender_email=event.sender_email,
        )
        return False
    # UNKNOWN. Nothing vouched for this address, so it names nobody here.
    logger.warning(
        "agent_surfaces.identity.email_sender_unauthenticated.degraded",
        platform=str(event.platform),
        sender_email=event.sender_email,
        resolved=False,
    )
    return False


class _CachedSender(Protocol):
    """What a stored external-user row has to be able to answer.

    A structural type rather than the repository's model, because this reads
    five attributes and is handed whatever `_upsert` returns -- which is
    annotated `Any`. Naming the five keeps that `Any` from spreading into the
    one place that decides whether a cached resolution may be believed.
    """

    resolved_user_id: UUID | None
    external_user_id: str | None
    email: str | None
    phone: str | None
    display_name: str | None


class SurfaceIdentityResolutionService:
    """Resolve an inbound platform message sender to an internal Lemma user.

    Resolution order
    ----------------
    1. Cache hit — ExternalSurfaceUser row already has a resolved_user_id, and
       that user is still live. Skipped entirely when the caller asked for a
       proven identity: the row does not record what proved it.
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

    def __init__(
        self,
        uow,
        external_user_repository: ExternalSurfaceUserRepository,
        *,
        user_directory: SurfaceUserDirectoryPort | None = None,
        verified_identity_lookup: Callable[
            [ParsedInboundSurfaceEvent], Awaitable[ResolvedSurfaceUser | None]
        ]
        | None = None,
        live_user_lookup: Callable[[UUID], Awaitable[UUID | None]] | None = None,
    ):
        self.uow = uow
        self.external_user_repository = external_user_repository
        self._users = user_directory or IdentityUserDirectoryAdapter(uow)
        self._verified_identity_lookup = (
            verified_identity_lookup or self._resolve_verified_identity
        )
        self._live_user_lookup = live_user_lookup or self._resolve_live_user

    async def _resolve_live_user(self, user_id: UUID) -> UUID | None:
        from app.modules.identity.contracts.surfaces import live_user_id

        return await live_user_id(self.uow, user_id)

    async def _resolve_verified_identity(
        self, event: ParsedInboundSurfaceEvent
    ) -> ResolvedSurfaceUser | None:
        from app.modules.agent_surfaces.services.verified_surface_identity import (
            resolve_shared_verified_identity,
        )

        return await resolve_shared_verified_identity(self.uow, event)

    async def resolve(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
        sender_profile: SurfaceSenderProfile | None = None,
        installation_id: UUID | None = None,
        require_proven_identity: bool = False,
    ) -> ResolvedSurfaceUser:
        if installation_id is not None and event.platform in (
            SurfacePlatform.SLACK,
            SurfacePlatform.TEAMS,
        ):
            from app.modules.agent_surfaces.services.verified_surface_identity import (
                resolve_shared_verified_identity,
            )

            verified = await resolve_shared_verified_identity(
                self.uow, event, installation_id
            )
        else:
            verified = await self._verified_identity_lookup(event)
        if verified is not None:
            return verified
        known = _KnownSender.of(sender_profile or SurfaceSenderProfile(), event)

        # ── 0. An email sender is only who they say they are if the receiving
        #      mail service said so ──────────────────────────────────────────
        #
        # Above the cache, and that placement is the whole control. On an email
        # surface the external_user_id *is* the From address, so a spoofed
        # message from an address that resolved once before would hit the cache
        # hit below and never reach a check placed with the other matches.
        if not _email_sender_is_believable(event):
            return ResolvedSurfaceUser(
                internal_user_id=None,
                external_user_id=known.external_user_id,
                email=known.email,
                display_name=known.display_name,
            )

        # ── 1. Upsert the ExternalSurfaceUser row with whatever we know ─────
        external_user = None
        if known.external_user_id:
            external_user = await self._upsert(event, known)
            cached = await self._cached_resolution(
                external_user, known, require_proven_identity
            )
            if cached is not None:
                return cached

        # ── 2-4. Match against Lemma users: telegram username, then email,
        #         then phone ─────────────────────────────────────────────────
        match = (
            await self._match_proven_sender(event, known)
            if require_proven_identity
            else await self._match_user_result(
                email=known.email,
                phone=known.phone,
                telegram_username=_telegram_username(event),
            )
        )

        # Persist the resolved_user_id so the next message is a cache hit.
        if known.external_user_id and match.cacheable:
            external_user = await self._upsert(
                event, known, resolved_user_id=match.user_id
            )

        return ResolvedSurfaceUser(
            internal_user_id=match.user_id,
            external_user_id=known.external_user_id,
            email=known.email,
            phone=known.phone,
            display_name=known.display_name
            or (external_user.display_name if external_user else None),
        )

    async def _cached_resolution(
        self,
        external_user: _CachedSender,
        known: "_KnownSender",
        require_proven_identity: bool,
    ) -> ResolvedSurfaceUser | None:
        """The answer this sender resolved to before, if it still stands.

        Two things the bare short-circuit this replaces got wrong, both of them
        because it answered above every check rather than beside them:

        *Liveness.* Each fresh lookup in `resolve` excludes deactivated and
        deleted accounts, because a match is what the agent run then executes
        as. A cached id skipped all of that, so deactivating somebody did not
        take their chat access away -- their next message resolved from here and
        ran as them. One indexed read by primary key re-asks identity the only
        question that can have changed.

        *Proof.* `require_proven_identity` means the caller is about to bind this
        platform account to that Lemma user permanently, so the match behind it
        has to be one the platform attested rather than one the sender asserted.
        The row records the id and not what produced it, so this re-derives --
        see `_cache_is_attested`.

        Refusing every cache hit on the proven path instead was tried and was
        wrong. `_match_proven_sender` matches WhatsApp and nothing else, so the
        cache is the only thing that recognises a Slack or Teams sender at all:
        without it every already-known person on those platforms was sent back
        through signup on their next message. Five e2e suites said so.
        """
        if not external_user.resolved_user_id:
            return None
        if not await self._live_user_lookup(external_user.resolved_user_id):
            return None
        if require_proven_identity and not await self._cache_is_attested(
            known, external_user.resolved_user_id
        ):
            return None
        return ResolvedSurfaceUser(
            internal_user_id=external_user.resolved_user_id,
            external_user_id=external_user.external_user_id,
            email=external_user.email,
            phone=external_user.phone,
            display_name=external_user.display_name,
        )

    async def _cache_is_attested(
        self, known: "_KnownSender", cached_user_id: UUID
    ) -> bool:
        """Is this cached resolution one the platform stands behind?

        The cache stores who a sender resolved to, never how, so a caller about
        to write a permanent binding cannot read it at face value. This re-runs
        the two matches whose input came from somewhere other than the sender --
        the profile email the platform's own API returned, and a mobile number
        already verified on the account -- and asks whether either still names
        the same person.

        Deliberately not the Telegram-username match, which is the reason this
        exists: `telegram_username` is free text on a Lemma profile that nobody
        confirms, so a handle that changes hands would otherwise let its new
        owner be bound to the previous one's account.

        Deliberately not `_match_user_by_phone` either, whose unverified
        fallback exists so an ordinary message can still be routed. Routing and
        binding are not the same permission, and only the verified half of that
        function is a proof.

        What this does not do is make a profile email proof of ownership. It is
        the platform's word, and a workspace administrator can set it; the
        `require_proven_identity` matcher refuses it outright for a sender with
        no cache entry, and that refusal stands. This is the narrower claim that
        an entry already written is still consistent with an attested field --
        which is strictly more than the unconditional short-circuit that shipped
        before it checked anything at all.
        """
        if known.email:
            by_email = await self._users.user_id_by_email(known.email)
            if by_email is not None and by_email == cached_user_id:
                return True
        if known.phone:
            candidates = _phone_lookup_candidates(known.phone)
            if candidates:
                ids = await self._users.user_ids_by_mobile_numbers(
                    candidates, verified=True
                )
                if ids == [cached_user_id]:
                    return True
        return False

    async def _match_proven_sender(
        self, event: ParsedInboundSurfaceEvent, known: _KnownSender
    ) -> _UserMatch:
        # A signed WhatsApp sender proves a phone already verified on the account.
        # Other unknown actors must complete private verification; profile email
        # and Telegram usernames may describe someone, but do not prove ownership.
        if event.platform != SurfacePlatform.WHATSAPP or not known.phone:
            return _UserMatch(None)
        ids = await self._users.user_ids_by_mobile_numbers(
            _phone_lookup_candidates(known.phone), verified=True
        )
        return _UserMatch(ids[0] if len(ids) == 1 else None)

    async def _upsert(
        self,
        event: ParsedInboundSurfaceEvent,
        known: "_KnownSender",
        **extra: Any,
    ) -> Any:
        """Write what we know about this sender, with whatever else is settled."""
        return await self.external_user_repository.upsert(
            platform=event.platform,
            tenant_id=event.tenant_id,
            external_user_id=known.external_user_id,
            email=known.email,
            phone=known.phone,
            display_name=known.display_name,
            raw_profile=known.raw_profile,
            **extra,
        )

    async def _match_user(
        self,
        *,
        email: str | None,
        phone: str | None,
        telegram_username: str | None = None,
    ) -> UUID | None:
        """Return the internal user_id for this sender, or None if not found."""
        return (
            await self._match_user_result(
                email=email,
                phone=phone,
                telegram_username=telegram_username,
            )
        ).user_id

    async def _match_user_result(
        self,
        *,
        email: str | None,
        phone: str | None,
        telegram_username: str | None = None,
    ) -> _UserMatch:
        # Telegram username first: a direct @username match links the sender
        # without the contact-share (phone) flow.
        if telegram_username:
            user_id = await self._match_user_by_telegram_username(telegram_username)
            if user_id:
                return _UserMatch(user_id)

        if email:
            user_id = await self._users.user_id_by_email(email)
            if user_id:
                return _UserMatch(user_id)

        if phone:
            phone_match = await self._match_user_by_phone(phone)
            if phone_match.user_id:
                return phone_match

        return _UserMatch(None)

    async def _match_user_by_telegram_username(self, username: str) -> UUID | None:
        cleaned = str(username or "").strip().lstrip("@").lower()
        if not cleaned:
            return None
        # The *live*-user lookup, not the "is this handle taken" one beside it:
        # a match here is what the run then executes as, so a deactivated or
        # deleted holder of the handle must resolve to nobody.
        return await self._users.user_id_by_telegram_username(cleaned)

    async def _match_user_by_phone(self, phone: str) -> _UserMatch:
        candidates = _phone_lookup_candidates(phone)
        if not candidates:
            return _UserMatch(None)
        # Prefer verified ownership. If verification is optional or has not yet
        # happened, a single eligible profile match still routes the surface.
        ids = await self._users.user_ids_by_mobile_numbers(candidates, verified=True)
        if len(ids) == 1:
            return _UserMatch(ids[0])
        if len(ids) > 1:
            logger.error(
                "agent_surfaces.identity.ambiguous_mobile_match",
                verification_state="verified",
                candidate_count=len(ids),
            )
            return _UserMatch(None)

        unverified_ids = await self._users.user_ids_by_mobile_numbers(
            candidates, verified=False
        )
        if len(unverified_ids) == 1:
            return _UserMatch(unverified_ids[0])
        if len(unverified_ids) > 1:
            logger.error(
                "agent_surfaces.identity.ambiguous_mobile_match",
                verification_state="unverified",
                candidate_count=len(unverified_ids),
            )
        return _UserMatch(None)


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
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    # Meta wa_id is E.164 without '+'. Telegram contacts may include or omit
    # it. Store one canonical representation while matching profile data by
    # digits so legacy formatting remains compatible.
    return f"+{digits}"


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
