"""Shared primitives for native surface receivers (pollers / socket clients).

Kept apart from the ``surface.receiver`` work handler so a per-platform runner
module can depend on the candidate shape, the receiver key and the inbox
without importing the handler -- which imports the runners, so the reverse
would be a cycle.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.credentials import SurfaceCredentials

from app.modules.agent_surfaces.domain.source_event_ids import native_source_event_id

import hashlib
import hmac
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import SurfacePlatform

logger = get_logger(__name__)


class NativeReceiverConflict(RuntimeError):
    """Another consumer already owns this upstream credential.

    The receiver's work-item lease only orders the workers of *one*
    deployment. Nothing stops a Desktop installation and Lemma Cloud
    from being configured with the same Telegram bot or Resend inbox, and each
    then takes its own lease from its own Redis and starts consuming.

    Upstream is the only place that can see both. Telegram says so outright --
    a second ``getUpdates`` gets 409 -- so a runner that has been refused for
    longer than a handover could explain raises this rather than returning as
    though it had finished its work.
    """


class NativeReceiverCredentialsRejected(NativeReceiverConflict):
    """Upstream refused the credential itself -- a revoked or deleted token.

    Retrying cannot fix that, and a poller that kept retrying every few seconds
    looked, from the outside, like a bot that had simply gone quiet. Standing
    down on the same cooldown as a conflict means the bot is retried after it,
    so a token restored in the meantime comes back without anyone intervening.
    """


class ReceiverFailureStreak:
    """Warn on the first failure of a streak, then at most once per interval.

    A receiver that fails keeps failing on every poll; a warning per poll would
    bury the log, and the debug line it replaced was dropped at `LOG_LEVEL=INFO`
    so a dead receiver left no trace at all. One warning when the streak starts,
    a reminder while it lasts, and a line when it ends.
    """

    def __init__(
        self,
        *,
        platform: SurfacePlatform,
        credential_label: str,
        repeat_after_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._platform = platform
        self._credential_label = credential_label
        self._repeat_after_seconds = repeat_after_seconds
        self._clock = clock
        self._failures = 0
        self._last_warned_at: float | None = None

    @property
    def failures(self) -> int:
        return self._failures

    def failed(self, exc: BaseException) -> None:
        self._failures += 1
        now = self._clock()
        if (
            self._last_warned_at is not None
            and now - self._last_warned_at < self._repeat_after_seconds
        ):
            return
        self._last_warned_at = now
        logger.warning(
            "agent_surfaces.native_receiver.poll_failed.degraded",
            platform=self._platform.value,
            credential_label=self._credential_label,
            failure_count=self._failures,
            exc_info=exc,
        )

    def succeeded(self) -> None:
        if self._failures:
            logger.info(
                "agent_surfaces.native_receiver.poll_recovered.observed",
                platform=self._platform.value,
                credential_label=self._credential_label,
                failure_count=self._failures,
            )
        self._failures = 0
        self._last_warned_at = None


class ReceiverRunner(Protocol):
    async def run(self) -> None:
        """Run the receiver until it is cancelled."""


ReceiverRunnerFactory = Callable[
    ["NativeReceiverCandidate", "ReceiverInbox"], ReceiverRunner
]


@dataclass(frozen=True)
class NativeReceiverCandidate:
    key: str
    platform: SurfacePlatform
    surface_ids: tuple[UUID, ...]
    credential_label: str
    credentials: SurfaceCredentials
    #: The pods the served surfaces belong to; one pod makes the receiver's
    #: work that pod's.
    pod_ids: tuple[UUID, ...] = ()

    @property
    def pod_id(self) -> UUID | None:
        return self.pod_ids[0] if len(set(self.pod_ids)) == 1 else None


def receiver_key(platform: str, label: str, secret: str) -> str:
    """A stable, opaque per-credential id for lease/dedup keys — not a password.

    The credential is the HMAC *key*, not the hashed input: this is a keyed
    fingerprint that identifies a credential group and detects rotation without
    storing or exposing the secret. A bare hash of the credential would read to
    scanners as (weak) password hashing, which this deliberately is not.
    """
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{platform}:{label}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:24]
    return f"{platform}:{label}:{digest}"


class ReceiverInbox:
    """Where a native receiver puts what it read, and keeps its place.

    Each upstream message goes through the same acceptor as a webhook
    (``InboundAcceptor``): classified, answered if it is a tap, stored as a
    ``surface_inbound_messages`` row with its ``surface.ingest`` item if it is a
    message. The receiver's cursor (a Telegram offset, a Resend email id) is
    written in that same transaction, so a crash can neither skip a message
    nor -- thanks to the row's unique key -- store one twice.
    """

    def __init__(
        self,
        candidate: NativeReceiverCandidate,
        *,
        uow_factory: Any | None = None,
    ) -> None:
        from app.core.infrastructure.db.session import async_session_maker
        from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory

        self.candidate = candidate
        self.uow_factory = uow_factory or SessionUnitOfWorkFactory(async_session_maker)

    async def load_cursor(self, legacy: Callable[[], Any] | None = None) -> str | None:
        """The stored cursor; on first run, the one Redis held before (once)."""
        from app.modules.agent_surfaces.infrastructure.repositories.inbound_message_repository import (  # noqa: E501
            ReceiverStateRepository,
        )

        async with self.uow_factory() as uow:
            repository = ReceiverStateRepository(uow.session)
            if await repository.exists(self.candidate.key):
                return await repository.load(self.candidate.key)
        if legacy is None:
            return None
        cursor = await legacy()
        if cursor is not None:
            await self.store_cursor(str(cursor))
            logger.info(
                "agent_surfaces.native_receiver.cursor_converted.observed",
                platform=self.candidate.platform.value,
                credential_label=self.candidate.credential_label,
            )
        return None if cursor is None else str(cursor)

    async def store_cursor(self, cursor: str | None) -> None:
        from app.modules.agent_surfaces.infrastructure.repositories.inbound_message_repository import (  # noqa: E501
            ReceiverStateRepository,
        )

        async with self.uow_factory() as uow:
            await ReceiverStateRepository(uow.session).store(self.candidate.key, cursor)

    async def accept(
        self,
        *,
        source: str,
        payload: dict[str, Any],
        cursor: str | None = None,
        surface_id: UUID | None = None,
        source_event_id: str | None = None,
        receiver_scope: str | None = None,
    ) -> Any:
        from app.modules.agent_surfaces.domain.ingress_request import (
            SurfaceDirectWebhookIngress,
            SurfacePlatformWebhookIngress,
        )
        from app.modules.agent_surfaces.infrastructure.repositories.inbound_message_repository import (  # noqa: E501
            ReceiverStateRepository,
        )
        from app.modules.agent_surfaces.services.inbound_acceptor import (
            InboundAcceptor,
            InboundDelivery,
        )

        headers = {
            "x-lemma-surface-event-mode": "native_receiver",
            "x-lemma-surface-receiver-key": self.candidate.key,
        }
        request = (
            SurfaceDirectWebhookIngress(
                surface_id=surface_id, payload=payload, headers=headers
            )
            if surface_id is not None
            else SurfacePlatformWebhookIngress(
                source=source,
                payload=payload,
                headers=headers,
                # Scoped to the surfaces this bot serves, so a custom bot's
                # update can't be attributed to another bot's surface.
                receiver_surface_ids=list(self.candidate.surface_ids) or None,
            )
        )
        key = self.candidate.key

        async def keep_place(uow: Any) -> None:
            if cursor is not None:
                await ReceiverStateRepository(uow.session).store(key, cursor)

        return await InboundAcceptor(self.uow_factory).accept(
            InboundDelivery(
                request=request,
                receiver_scope=receiver_scope or key,
                source_event_id=source_event_id
                or native_source_event_id(source, payload, receiver_key=key),
                platform=SurfacePlatform.from_source(source),
                pod_id=self.candidate.pod_id,
            ),
            after_store=keep_place,
        )
