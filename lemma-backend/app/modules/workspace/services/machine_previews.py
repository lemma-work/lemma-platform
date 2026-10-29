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
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, update

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.workspace.config import workspace_settings
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
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
            if ttl_seconds
            else None
        )
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


def _key() -> bytes:
    secret = workspace_settings.runtime_credential_key or ""
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


def _verify(token: str, *, kind: str, label: str) -> bool:
    body, _, signature = token.partition(".")
    if not body or not signature:
        return False
    expected = (
        base64.urlsafe_b64encode(
            hmac.new(_key(), body.encode(), hashlib.sha256).digest()
        )
        .decode()
        .rstrip("=")
    )
    if not hmac.compare_digest(expected, signature):
        return False
    try:
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError:
        return False
    return (
        claims.get("k") == kind
        and claims.get("l") == label
        and int(claims.get("e", 0)) > int(datetime.now(timezone.utc).timestamp())
    )


def mint_ticket(label: str, user_id: UUID) -> str:
    expires = datetime.now(timezone.utc) + timedelta(seconds=TICKET_SECONDS)
    return _sign(
        {"k": "ticket", "l": label, "u": str(user_id), "e": int(expires.timestamp())}
    )


def ticket_is_valid(token: str, label: str) -> bool:
    return _verify(token, kind="ticket", label=label)


def mint_cookie(label: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(seconds=COOKIE_SECONDS)
    return _sign({"k": "cookie", "l": label, "e": int(expires.timestamp())})


def cookie_is_valid(token: str, label: str) -> bool:
    return _verify(token, kind="cookie", label=label)


def open_url(label: str) -> str:
    """Where a private preview sends a visitor without its cookie: the API,
    which has their session and mints the ticket."""
    return f"{settings.api_url.rstrip('/')}/machine-previews/{label}:open"
