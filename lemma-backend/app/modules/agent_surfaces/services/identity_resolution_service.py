from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.models import SurfaceSenderProfile
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_manager_repository import (  # noqa: E501
    SlackManagerRepository,
)
from app.modules.agent_surfaces.domain.ports import SurfaceUserDirectoryPort
from app.modules.agent_surfaces.infrastructure.adapters.user_directory_adapter import (
    IdentityUserDirectoryAdapter,
)
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.phone_numbers import (
    normalize_phone_number as _normalize_phone_number,
    phone_lookup_candidates as _phone_lookup_candidates,
)
from app.modules.agent_surfaces.services.email_sender_trust import (
    email_sender_is_believable as _email_sender_is_believable,
)
from app.modules.agent_surfaces.services.slack_cached_identity import (
    forgot_an_unproven_slack_answer,
)

logger = get_logger(__name__)

#: Where the sender's number is the platform's word and not the sender's. A
#: WhatsApp sender id *is* the number Meta delivered from; a Telegram number
#: arrives only in a contact the sender shared of themselves. A Slack profile
#: phone and a Teams ``mobilePhone`` are fields their owner types, so matching
#: them would hand a member's identity to whoever typed that member's number.
_PHONE_ATTESTED = frozenset({SurfacePlatform.WHATSAPP, SurfacePlatform.TELEGRAM})


def _email_may_match(platform: SurfacePlatform) -> bool:
    """Is this platform's sender email good enough to name a Lemma user?

    Not Slack's: a workspace admin can set anybody's profile email, so a Slack
    sender is somebody only on a proof (Lemma's app's, or the installation's
    own verification). Teams' is the UPN, and only on a domain the tenant
    verified (``tenant_verified_address``); an email surface's sender has
    already been authenticated by the receiving mail service.
    """
    return platform is not SurfacePlatform.SLACK


@dataclass(frozen=True)
class _UserMatch:
    user_id: UUID | None


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
       proven identity, unless the platform still attests the match. On Slack
       only while Slack's proof says the same (``slack_cached_identity``).
    2. Email match — profile email (fetched from platform API) matched against
       the users table. Never on Slack, whose profile email an admin can set:
       a Slack sender is matched only on a proof.
    3. Phone match — only where the platform attests the sender's number
       (WhatsApp, and Telegram's contact-share flow), against numbers verified
       on a profile. An unverified profile number matches only where the
       deployment has opted in (``surface_allow_unverified_phone_match``) and
       exactly one profile claims it.

    On a surface whose deliveries are signed with a secret its customer holds
    (``customer_held_secrets``) none of this runs: only a proof made through
    that same installation names anybody.

    A Telegram sender is matched on nothing else: Telegram gives no email, and
    its @username is not proof of anything -- anyone can claim any free handle,
    and a handle given up is taken by a stranger. An unresolved Telegram sender
    is asked to share their own contact, and the number in it is what matches.

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
        profile_unavailable: bool = False,
        customer_held_secret: bool = False,
    ) -> ResolvedSurfaceUser:
        """``profile_unavailable``: the platform could not be asked who sent
        this, so a miss here is not an answer and is not remembered as one.

        ``customer_held_secret``: the delivery verified against a secret a
        person holds (``signed_with_a_customer_secret``), so its sender id is
        theirs to choose -- see `_resolve_on_a_customer_secret`."""
        if customer_held_secret:
            return await self._resolve_on_a_customer_secret(
                event, sender_profile, installation_id
            )
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
                external_user, known, require_proven_identity, event
            )
            if cached is not None:
                return cached

        # ── 2-3. Match against Lemma users: Slack's proof, email, then phone ─
        match = await self._slack_proof(event, known)
        if match.user_id is None:
            match = (
                await self._match_proven_sender(event, known)
                if require_proven_identity
                else await self._match_user_result(
                    event.platform, email=known.email, phone=known.phone
                )
            )
            match = await self._unless_proven_elsewhere(event, known, match)

        # Persist the resolved_user_id so the next message is a cache hit.
        if known.external_user_id and not profile_unavailable:
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

    async def _resolve_on_a_customer_secret(
        self,
        event: ParsedInboundSurfaceEvent,
        sender_profile: SurfaceSenderProfile | None,
        installation_id: UUID | None,
    ) -> ResolvedSurfaceUser:
        """Who sent a delivery that the secret's holder could have written.

        Whoever holds a bring-your-own app's signing secret can sign a body
        naming any user of the workspace, or any phone number, and it verifies.
        Every other answer `resolve` has is keyed on that claimed id and was
        made somewhere else: the external-user cache and Lemma's own app's
        proofs are per workspace, not per app; the shared bot's bindings are
        the shared bot's; an email or a phone match is only the claim again.
        Honouring any of them let the holder act as any member somebody proved
        anywhere. What is left is a binding made through this installation --
        the person verified their mailbox in a private chat with this very app
        -- so the holder can only be somebody who chose to trust their app.

        The cache is still told what the platform's profile says, but never
        who the sender is: writing a resolution here would let a forged
        delivery speak for every surface in the workspace, and writing ``None``
        would let one erase somebody's.
        """
        known = _KnownSender.of(sender_profile or SurfaceSenderProfile(), event)
        if installation_id is not None and not event.platform.is_email:
            from app.modules.agent_surfaces.services import verified_surface_identity

            verified = await verified_surface_identity.resolve_shared_verified_identity(
                self.uow, event, installation_id
            )
            if verified is not None:
                return verified
        if known.external_user_id:
            await self._upsert(event, known)
        return ResolvedSurfaceUser(
            internal_user_id=None,
            external_user_id=known.external_user_id,
            email=known.email,
            phone=known.phone,
            display_name=known.display_name,
        )

    async def _cached_resolution(
        self,
        external_user: _CachedSender,
        known: "_KnownSender",
        require_proven_identity: bool,
        event: ParsedInboundSurfaceEvent,
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
        proven = (await self._slack_proof(event, known)).user_id
        if await forgot_an_unproven_slack_answer(
            self.external_user_repository, event, external_user, proven=proven
        ):
            return None
        if (
            require_proven_identity
            and proven != external_user.resolved_user_id
            and not await self._cache_is_attested(
                event.platform, known, external_user.resolved_user_id
            )
        ):
            return None
        if await self._cache_rests_on_an_unverified_number(external_user, known, event):
            return None
        return ResolvedSurfaceUser(
            internal_user_id=external_user.resolved_user_id,
            external_user_id=external_user.external_user_id,
            email=external_user.email,
            phone=external_user.phone,
            display_name=external_user.display_name,
        )

    async def _cache_is_attested(
        self, platform: SurfacePlatform, known: "_KnownSender", cached_user_id: UUID
    ) -> bool:
        """Is this cached resolution one the platform stands behind?

        The cache stores who a sender resolved to, never how, so a caller about
        to write a permanent binding cannot read it at face value. This re-runs
        the two matches whose input came from somewhere other than the sender --
        the profile email the platform's own API returned, and a mobile number
        already verified on the account -- and asks whether either still names
        the same person.

        What this does not do is make a profile email proof of ownership. It is
        the platform's word, and a workspace administrator can set it; the
        `require_proven_identity` matcher refuses it outright for a sender with
        no cache entry, and that refusal stands. This is the narrower claim that
        an entry already written is still consistent with an attested field --
        which is strictly more than the unconditional short-circuit that shipped
        before it checked anything at all.

        Each field counts only where it may match at all: on Slack neither the
        profile email nor the profile phone does, so a Slack entry stands here
        only on Slack's own proof (`_slack_proof`, asked by the caller).
        """
        if known.email and _email_may_match(platform):
            by_email = await self._users.user_id_by_email(known.email)
            if by_email is not None and by_email == cached_user_id:
                return True
        if known.phone and platform in _PHONE_ATTESTED:
            candidates = _phone_lookup_candidates(known.phone)
            if candidates:
                ids = await self._users.user_ids_by_mobile_numbers(
                    candidates, verified=True
                )
                if ids == [cached_user_id]:
                    return True
        return False

    async def _cache_rests_on_an_unverified_number(
        self,
        external_user: _CachedSender,
        known: "_KnownSender",
        event: ParsedInboundSurfaceEvent,
    ) -> bool:
        """Was this cached resolution made on a number nobody verified, and only that?

        A row records the number it was matched on, not whether the profile's
        number was verified then, so a resolution made while unverified numbers were
        accepted would outlive the setting that allowed it: turning it off would
        change nothing for anyone already matched. Where the deployment does not
        accept them, a cached resolution is dropped when the *only* thing
        linking this sender to that user is an unverified profile number -- and
        kept when anything else does: a verified number, the profile email, or
        nothing about the phone at all.

        Dropping it sends the sender through ordinary matching, which now
        resolves nobody and clears the row.
        """
        if surface_settings.surface_allow_unverified_phone_match:
            return False
        if event.platform not in (SurfacePlatform.WHATSAPP, SurfacePlatform.TELEGRAM):
            return False
        cached_user_id = external_user.resolved_user_id
        phone = known.phone or external_user.phone
        candidates = _phone_lookup_candidates(phone) if phone else []
        if cached_user_id is None or not candidates:
            return False
        unverified = await self._users.user_ids_by_mobile_numbers(
            candidates, verified=False
        )
        if cached_user_id not in unverified:
            return False
        if await self._cache_is_attested(event.platform, known, cached_user_id):
            return False
        verified = await self._users.user_ids_by_mobile_numbers(
            candidates, verified=True
        )
        return cached_user_id not in verified

    async def _slack_proof(
        self, event: ParsedInboundSurfaceEvent, known: _KnownSender
    ) -> _UserMatch:
        """The Lemma person Slack proved this sender to be in this workspace.

        Through Lemma's own app (``slack_workspace_people``): the installer's
        grant, or Sign in with Slack. A proof is written through to this
        cache, so it is usually a cache hit before this is asked; this is for
        a row something cleared since -- a profile phone change clears every
        platform identity of its person -- which an email that differs from
        the Lemma one would otherwise answer as nobody. Proof says who they
        are, not whether they are in a pod: membership still decides routing.
        """
        if (
            event.platform is not SurfacePlatform.SLACK
            or not event.tenant_id
            or not known.external_user_id
        ):
            return _UserMatch(None)
        user_id = await SlackManagerRepository(self.uow.session).person(
            event.tenant_id, known.external_user_id
        )
        if user_id is None or await self._live_user_lookup(user_id) is None:
            return _UserMatch(None)
        return _UserMatch(user_id)

    async def _unless_proven_elsewhere(
        self, event: ParsedInboundSurfaceEvent, known: _KnownSender, match: _UserMatch
    ) -> _UserMatch:
        """No match where Slack proved that person to be another Slack user here.

        A Slack profile email is the workspace's word, and a workspace admin
        can set anybody's: once Slack itself has said which of its users a
        Lemma person is, another user carrying their email is not them.
        """
        if (
            match.user_id is None
            or event.platform is not SurfacePlatform.SLACK
            or not event.tenant_id
            or not known.external_user_id
        ):
            return match
        proven_as = await SlackManagerRepository(self.uow.session).slack_user_for(
            event.tenant_id, match.user_id
        )
        if proven_as is None or proven_as == known.external_user_id:
            return match
        logger.warning(
            "agent_surfaces.identity.email_of_proven_person_refused.observed",
            platform=event.platform.value,
        )
        return _UserMatch(None)

    async def _match_proven_sender(
        self, event: ParsedInboundSurfaceEvent, known: _KnownSender
    ) -> _UserMatch:
        # A signed WhatsApp sender proves a phone already verified on the account.
        # Other unknown actors must complete private verification; a profile
        # email may describe someone, but does not prove ownership.
        if event.platform != SurfacePlatform.WHATSAPP or not known.phone:
            return _UserMatch(None)
        ids = await self._users.user_ids_by_mobile_numbers(
            _phone_lookup_candidates(known.phone), verified=True
        )
        if len(ids) != 1:
            return _UserMatch(None)
        return _UserMatch(ids[0])

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

    async def _match_user_result(
        self,
        platform: SurfacePlatform,
        *,
        email: str | None,
        phone: str | None,
    ) -> _UserMatch:
        if email and _email_may_match(platform):
            user_id = await self._users.user_id_by_email(email)
            if user_id:
                return _UserMatch(user_id)

        # The verified-ownership check below is about the *profile's* side of
        # the match; it says nothing about the sender's side, which has to be
        # the platform's word before it is compared with anything.
        if phone and platform in _PHONE_ATTESTED:
            phone_match = await self._match_user_by_phone(phone)
            if phone_match.user_id:
                return phone_match

        return _UserMatch(None)

    async def _match_user_by_phone(self, phone: str) -> _UserMatch:
        candidates = _phone_lookup_candidates(phone)
        if not candidates:
            return _UserMatch(None)
        # Verified ownership first. The sender's number is attested by the
        # platform, but the profile's is a claim until its owner proves it, and
        # anyone can write another person's number on their own profile.
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
        # Matching on the claim would send that person's messages -- and the
        # agent's replies to them -- to whoever wrote it, so a sender with no
        # verified match is a stranger unless the deployment has accepted that
        # trade (see the setting) and exactly one profile claims the number.
        if not surface_settings.surface_allow_unverified_phone_match:
            return _UserMatch(None)
        unverified_ids = await self._users.user_ids_by_mobile_numbers(
            candidates, verified=False
        )
        if len(unverified_ids) == 1:
            # Logged, because the sender was routed on a claim nobody proved.
            logger.warning(
                "agent_surfaces.identity.unverified_phone_match_used.observed",
            )
            return _UserMatch(unverified_ids[0])
        if len(unverified_ids) > 1:
            logger.error(
                "agent_surfaces.identity.ambiguous_mobile_match",
                verification_state="unverified",
                candidate_count=len(unverified_ids),
            )
        return _UserMatch(None)
