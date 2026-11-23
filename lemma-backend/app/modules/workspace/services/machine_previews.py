"""Previews: a port on a machine at its own subdomain, until revoked or expired.

``p-<label>.<MACHINE_PREVIEW_DOMAIN>`` rather than a path under the API: a dev
server (Vite, Next) serves root-absolute assets and opens its hot-reload socket
at the root of its own origin, and a path prefix breaks both. A subdomain per
preview is also an origin per preview, so one preview's page can never read
another's cookies or the API's.

Two kinds:

- ``private``: for people who may see the machine. The first visit bounces
  through the API, which knows who they are, and comes back with a one-time
  ticket the preview host exchanges for a cookie of its own. The API's
  session never reaches the preview host.
- ``link``: anyone with the URL, for at most 24 hours. The label is random
  and unguessable, because here it is the credential.

The proxy looks the label up on every request, through a cache of a few
seconds, so revoking a preview stops it almost at once.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import string
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.domain.machine import MachineKind
from app.modules.workspace.domain.machine_errors import (
    MachineConflict,
    MachineNotFound,
)
from app.modules.workspace.infrastructure.machine_models import MachinePreviewModel

_LABEL_ALPHABET = string.ascii_lowercase + string.digits
_LABEL_LENGTH = 20
#: How long a revoked preview may go on answering from the cache.
_CACHE_SECONDS = 5
TICKET_SECONDS = 60
#: The preview host's own cookie: hours, not weeks. Reopening from Lemma mints
#: another.
COOKIE_SECONDS = 12 * 3600
COOKIE_NAME = "lemma_preview"
AUTH_PATH = "/__lemma/auth"

_cache: RedisJsonCache[dict[str, object]] | None = None


def _preview_cache() -> RedisJsonCache[dict[str, object]]:
    global _cache
    if _cache is None:
        _cache = RedisJsonCache(
            settings.redis_url, "machine:preview:v1", ttl_seconds=_CACHE_SECONDS
        )
    return _cache


@dataclass(frozen=True, slots=True)
class Preview:
    id: UUID
    sandbox_id: UUID
    label: str
    port: int
    visibility: str
    created_by: UUID
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None

    @property
    def live(self) -> bool:
        now = datetime.now(timezone.utc)
        return self.revoked_at is None and (
            self.expires_at is None or self.expires_at > now
        )

    @property
    def url(self) -> str:
        return preview_url(self.label)


def previews_enabled() -> bool:
    return bool((workspace_settings.machine_preview_domain or "").strip())


def preview_url(label: str, path: str = "/") -> str:
    domain = (workspace_settings.machine_preview_domain or "").strip()
    return f"{workspace_settings.machine_preview_scheme}://p-{label}.{domain}{path}"


def label_from_host(host: str) -> str | None:
    """The preview label in a ``Host``, or None for any other host."""
    domain = (workspace_settings.machine_preview_domain or "").strip().lower()
    if not domain:
        return None
    bare_domain = domain.split(":", 1)[0]
    bare_host = host.split(":", 1)[0].strip().lower()
    suffix = f".{bare_domain}"
    if not bare_host.endswith(suffix):
        return None
    sub = bare_host[: -len(suffix)]
    if not sub.startswith("p-") or "." in sub:
        return None
    label = sub[2:]
    if len(label) != _LABEL_LENGTH or any(c not in _LABEL_ALPHABET for c in label):
        return None
    return label


def _entity(row: MachinePreviewModel) -> Preview:
    return Preview(
        id=row.id,
        sandbox_id=row.sandbox_id,
        label=row.label,
        port=row.port,
        visibility=row.visibility,
        created_by=row.created_by,
        created_at=row.created_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
    )


def refuse_unpreviewable_port(kind: MachineKind, port: int) -> None:
    """The runtime, the browser relay, VNC, the streamer and DevTools are not
    previews.

    A preview of 8080 reached the workspace runtime -- which asked for its own
    credential, so nothing leaked, but a URL that can only ever answer 401 is
    a preview nobody can use. A preview of DevTools would be the signed-in
    browser handed to whoever holds the link; the relay refuses it too, but on
    E2B and a private Docker network a preview does not go through the relay.
    A connected machine is somebody's own computer: there 8080 is as likely as
    not the dev server they meant, and DevTools is their real browser.
    """
    from sandbox_runtime.workspace.port_relay import (
        DEVTOOLS_DEFAULT_PORTS,
        REFUSED_PREVIEW_PORTS,
    )

    refused = (
        DEVTOOLS_DEFAULT_PORTS
        if kind is MachineKind.CONNECTED
        else REFUSED_PREVIEW_PORTS
    )
    if port in refused:
        raise MachineConflict(
            f"Port {port} is one the machine itself uses. Run the server on "
            "another port and preview that.",
            code="MACHINE_PORT_RESERVED",
        )


class MachinePreviews:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def create(
        self,
        sandbox_id: UUID,
        *,
        port: int,
        visibility: str,
        ttl_seconds: int | None,
        created_by: UUID,
    ) -> Preview:
        if not previews_enabled():
            raise MachineConflict(
                "Previews are off on this deployment: MACHINE_PREVIEW_DOMAIN is not set.",
                code="MACHINE_PREVIEWS_DISABLED",
            )
        ceiling = workspace_settings.machine_preview_max_ttl_seconds
        if visibility == "link":
            ttl_seconds = min(ttl_seconds or ceiling, ceiling)
        # Every preview ends. A private one with no ttl used to live as long as
        # its machine; it now gets the same default a link does.
        ttl_seconds = ttl_seconds or ceiling
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        async with self._uow_factory() as uow:
            row = MachinePreviewModel(
                sandbox_id=sandbox_id,
                label="".join(
                    secrets.choice(_LABEL_ALPHABET) for _ in range(_LABEL_LENGTH)
                ),
                port=port,
                visibility=visibility,
                created_by=created_by,
                expires_at=expires_at,
            )
            uow.session.add(row)
            await uow.session.flush()
            preview = _entity(row)
            await uow.commit()
        return preview

    async def list(self, sandbox_id: UUID) -> list[Preview]:
        async with self._uow_factory() as uow:
            rows = (
                await uow.session.scalars(
                    select(MachinePreviewModel)
                    .where(
                        MachinePreviewModel.sandbox_id == sandbox_id,
                        MachinePreviewModel.revoked_at.is_(None),
                    )
                    .order_by(MachinePreviewModel.created_at.desc())
                    .limit(200)
                )
            ).all()
        return [p for p in map(_entity, rows) if p.live]

    async def revoke(self, sandbox_id: UUID, preview_id: UUID) -> Preview:
        async with self._uow_factory() as uow:
            row = await uow.session.scalar(
                select(MachinePreviewModel).where(
                    MachinePreviewModel.id == preview_id,
                    MachinePreviewModel.sandbox_id == sandbox_id,
                )
            )
            if row is None:
                raise MachineNotFound(str(preview_id))
            await uow.session.execute(
                update(MachinePreviewModel)
                .where(MachinePreviewModel.id == preview_id)
                .values(revoked_at=datetime.now(timezone.utc))
            )
            label = row.label
            await uow.commit()
        await _preview_cache().delete(label)
        found = await self.by_label(label, cached=False)
        assert found is not None
        return found

    async def revoke_all(self, sandbox_id: UUID) -> int:
        """Revoke every live preview of a machine -- it is being deleted.

        The cached lookups go too, so the URLs stop answering at once rather
        than after the cache's few seconds.
        """
        async with self._uow_factory() as uow:
            labels = await revoke_machine_previews(uow.session, sandbox_id)
            await uow.commit()
        await forget_cached_previews(labels)
        return len(labels)

    async def by_label(self, label: str, *, cached: bool = True) -> Preview | None:
        cache = _preview_cache()
        if cached:
            hit = await cache.get_json(label)
            if hit:
                return _from_json(hit)
        async with self._uow_factory() as uow:
            row = await uow.session.scalar(
                select(MachinePreviewModel).where(MachinePreviewModel.label == label)
            )
        if row is None:
            return None
        preview = _entity(row)
        await cache.set_json(label, _to_json(preview))
        return preview


async def revoke_machine_previews(session: AsyncSession, sandbox_id: UUID) -> list[str]:
    """Mark a machine's unrevoked previews revoked, in the caller's
    transaction; the labels, for :func:`forget_cached_previews` after commit.

    One statement: the labels come back from the update itself.
    """
    revoked = await session.execute(
        update(MachinePreviewModel)
        .where(
            MachinePreviewModel.sandbox_id == sandbox_id,
            MachinePreviewModel.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(timezone.utc))
        .returning(MachinePreviewModel.label)
    )
    return list(revoked.scalars().all())


async def forget_cached_previews(labels: list[str]) -> None:
    cache = _preview_cache()
    for label in labels:
        await cache.delete(label)


def _to_json(preview: Preview) -> dict[str, object]:
    data = asdict(preview)
    return {
        key: (
            value.isoformat()
            if isinstance(value, datetime)
            else str(value)
            if isinstance(value, UUID)
            else value
        )
        for key, value in data.items()
    }


def _from_json(data: dict[str, object]) -> Preview:
    def when(value: object) -> datetime | None:
        return datetime.fromisoformat(str(value)) if value else None

    return Preview(
        id=UUID(str(data["id"])),
        sandbox_id=UUID(str(data["sandbox_id"])),
        label=str(data["label"]),
        port=int(str(data["port"])),
        visibility=str(data["visibility"]),
        created_by=UUID(str(data["created_by"])),
        created_at=when(data["created_at"]) or datetime.now(timezone.utc),
        expires_at=when(data.get("expires_at")),
        revoked_at=when(data.get("revoked_at")),
    )


# --- tickets and cookies -------------------------------------------------------


class PreviewKeyMissing(RuntimeError):
    """No secret to sign preview tickets and cookies with: refuse, never sign
    with a key anybody could compute."""


def _key() -> bytes:
    secret = (workspace_settings.runtime_credential_key or "").strip()
    if not secret:
        raise PreviewKeyMissing(
            "WORKSPACE_RUNTIME_CREDENTIAL_KEY is not set; previews cannot be signed"
        )
    return hashlib.sha256(f"machine-preview:{secret}".encode()).digest()


def _sign(claims: dict[str, object]) -> str:
    body = (
        base64.urlsafe_b64encode(
            json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )
    mac = hmac.new(_key(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{base64.urlsafe_b64encode(mac).decode().rstrip('=')}"


def _verify(token: str, *, kind: str, label: str) -> dict[str, object] | None:
    """The claims of a valid, unexpired token of ``kind`` for ``label``."""
    body, _, signature = token.partition(".")
    if not body or not signature:
        return None
    expected = (
        base64.urlsafe_b64encode(
            hmac.new(_key(), body.encode(), hashlib.sha256).digest()
        )
        .decode()
        .rstrip("=")
    )
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        expires = int(claims.get("e", 0))
    except ValueError, TypeError, AttributeError:
        return None
    if (
        claims.get("k") != kind
        or claims.get("l") != label
        or expires <= int(datetime.now(timezone.utc).timestamp())
    ):
        return None
    return claims


def _user_of(claims: dict[str, object] | None) -> UUID | None:
    if claims is None:
        return None
    try:
        return UUID(str(claims.get("u")))
    except ValueError:
        return None


def mint_ticket(label: str, user_id: UUID) -> str:
    expires = datetime.now(timezone.utc) + timedelta(seconds=TICKET_SECONDS)
    return _sign(
        {
            "k": "ticket",
            "l": label,
            "u": str(user_id),
            "j": secrets.token_urlsafe(16),
            "e": int(expires.timestamp()),
        }
    )


_tickets: RedisJsonCache[dict[str, object]] | None = None


def _ticket_store() -> RedisJsonCache[dict[str, object]]:
    global _tickets
    if _tickets is None:
        _tickets = RedisJsonCache(
            settings.redis_url, "machine:preview:ticket:v1", ttl_seconds=TICKET_SECONDS
        )
    return _tickets


async def redeem_ticket(token: str, label: str) -> UUID | None:
    """The person a ticket was minted for, once: a second use is refused.

    The ticket rides in a URL, so it can end up in a log, a history or a
    Referer; spending its id in Redis on first use is what makes it one-time.
    """
    claims = _verify(token, kind="ticket", label=label)
    user_id = _user_of(claims)
    jti = str((claims or {}).get("j") or "")
    if user_id is None or not jti:
        return None
    first = await _ticket_store().set_raw_if_absent(
        jti, "1", ttl_seconds=TICKET_SECONDS + 5
    )
    return user_id if first else None


def mint_cookie(label: str, user_id: UUID) -> str:
    expires = datetime.now(timezone.utc) + timedelta(seconds=COOKIE_SECONDS)
    return _sign(
        {"k": "cookie", "l": label, "u": str(user_id), "e": int(expires.timestamp())}
    )


def cookie_user(token: str, label: str) -> UUID | None:
    """Whose preview cookie this is, when it is valid for ``label``."""
    return _user_of(_verify(token, kind="cookie", label=label))


#: How long "this person may still use this machine" is believed. Someone
#: removed from the pod loses a preview within this long.
ACCESS_RECHECK_SECONDS = 60

_access: RedisJsonCache[dict[str, object]] | None = None


def _access_cache() -> RedisJsonCache[dict[str, object]]:
    global _access
    if _access is None:
        _access = RedisJsonCache(
            settings.redis_url,
            "machine:preview:access:v1",
            ttl_seconds=ACCESS_RECHECK_SECONDS,
        )
    return _access


def _use_redis_for_tests(redis) -> None:
    """Point every preview store at ``redis`` (a fake), or back at the real
    one with None."""
    for store in (_preview_cache(), _ticket_store(), _access_cache()):
        store._redis = redis


async def may_still_use(
    sandbox_id: UUID, user_id: UUID, check: Callable[[], Awaitable[bool]]
) -> bool:
    """Whether ``user_id`` may still use the machine, through a short cache.

    ``check`` asks the resolver; its answer, yes or no, is kept for
    :data:`ACCESS_RECHECK_SECONDS`.
    """
    cache = _access_cache()
    key = f"{sandbox_id}:{user_id}"
    hit = await cache.get_raw(key)
    if hit in ("1", "0"):
        return hit == "1"
    allowed = await check()
    await cache.set_raw(key, "1" if allowed else "0")
    return allowed


# --- what crosses the proxy -----------------------------------------------------

#: Lemma's own session cookies. With a session cookie scoped to a parent
#: domain they reach a preview's host too, and a preview is somebody's dev
#: server: it must never see them, nor be able to set them.
_LEMMA_COOKIES = frozenset(
    {
        "sAccessToken",
        "sRefreshToken",
        "sFrontToken",
        "sAntiCsrf",
        "sIdRefreshToken",
    }
)


def is_lemma_cookie(name: str) -> bool:
    name = name.strip()
    lowered = name.lower()
    return (
        name in _LEMMA_COOKIES
        or name == COOKIE_NAME
        or lowered.startswith(("st-", "lemma_"))
    )


def forwarded_cookie(cookie_header: str | None) -> str | None:
    """The visitor's cookies a preview may see: its own, without Lemma's
    session cookies or the preview's access cookie."""
    if not cookie_header:
        return None
    kept = []
    for part in cookie_header.split(";"):
        part = part.strip()
        if part and not is_lemma_cookie(part.split("=", 1)[0]):
            kept.append(part)
    return "; ".join(kept) or None


def upstream_set_cookie(value: str) -> str | None:
    """An upstream ``Set-Cookie`` as the visitor's browser may receive it.

    ``Domain`` is dropped, so the cookie stays on the host that served it: a
    sandbox's server could otherwise set cookies for the preview domain's
    parent -- every other preview, or Lemma's own host. A cookie named like
    one of Lemma's is dropped entirely.
    """
    name_value, *attributes = value.split(";")
    name = name_value.split("=", 1)[0]
    if not name.strip() or is_lemma_cookie(name):
        return None
    kept = [
        attribute
        for attribute in attributes
        if attribute.split("=", 1)[0].strip().lower() != "domain"
    ]
    return ";".join([name_value, *kept])


def open_url(label: str) -> str:
    """Where a private preview sends a visitor without its cookie: the API,
    which has their session and mints the ticket."""
    return f"{settings.api_url.rstrip('/')}/machine-previews/{label}:open"
