"""Short-lived URLs for datastore objects (original files and rendered pages).

Two backends, one interface:
- **GCS**: a real signed URL (``storage.get_signed_url`` → object signing). The
  client fetches the bytes straight from the bucket.
- **Local filesystem** (obstore ``LocalStore`` can't sign): a stateless,
  HMAC-signed token embedding ``(object_key, expiry)`` and a backend URL at
  ``/public/datastore/files`` that validates the token and streams the bytes.

URLs are cached in Redis (best-effort) so repeat requests don't re-sign.

These URLs are what we persist in agent messages (so a transcript can show a
page image) — never the image bytes themselves: the model receives bytes inline
via the tool's ``ToolReturn.content``, while the DB only stores the URL.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote
from uuid import UUID

from app.core.config import settings
from app.core.object_storage import storage_supports_native_signed_urls
from app.core.crypto import get_secret_signer
from app.modules.datastore.config import datastore_settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger
from app.modules.datastore.domain.file_entities import DatastoreFileEntity
from app.modules.datastore.domain.ports import DatastoreStoragePort
from app.modules.datastore.services.files.projection import datastore_storage_key

logger = get_logger(__name__)

#: Signing purpose for the unified signer (HKDF subkey label).
_PURPOSE = "datastore-file-url"
PUBLIC_FILE_PATH = "/public/datastore/files"


class InvalidFileUrlToken(Exception):
    """Raised when a file-URL token is malformed, tampered, or expired."""


@dataclass(frozen=True, slots=True)
class ObjectTokenClaims:
    object_key: str
    expires_at_epoch: int
    content_sha256: str | None = None


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def mint_object_token(
    *,
    object_key: str,
    expires_at_epoch: int,
    content_sha256: str | None = None,
) -> str:
    payload = json.dumps(
        {"k": object_key, "e": int(expires_at_epoch), "h": content_sha256},
        separators=(",", ":"),
    ).encode("utf-8")
    # signer.sign returns "<kid>.<sig>"; token is "<payload>.<kid>.<sig>".
    return f"{_b64e(payload)}.{get_secret_signer().sign(_PURPOSE, payload)}"


def verify_object_token_claims(
    token: str, *, now_epoch: int | None = None
) -> ObjectTokenClaims:
    """Return validated object-token claims."""
    now = now_epoch if now_epoch is not None else int(time.time())
    try:
        payload_b64, signature = token.split(".", 1)
        payload = _b64d(payload_b64)
        # Tokens are "<kid>.<sig>" and verify via the unified signer (HKDF off the
        # required SECRET_ENCRYPTION_KEY). There is no insecure dev-secret
        # fallback: a non-local deployment without the key fails closed rather
        # than accepting tokens forged with a public constant.
        if not get_secret_signer().verify(_PURPOSE, payload, signature):
            raise InvalidFileUrlToken("signature mismatch")
        data = json.loads(payload)
        expires_at_epoch = int(data["e"])
        if expires_at_epoch < now:
            raise InvalidFileUrlToken("token expired")
        return ObjectTokenClaims(
            object_key=str(data["k"]),
            expires_at_epoch=expires_at_epoch,
            content_sha256=(str(data["h"]) if data.get("h") else None),
        )
    except InvalidFileUrlToken:
        raise
    except Exception as exc:  # malformed b64/json
        raise InvalidFileUrlToken("malformed token") from exc


def verify_object_token(token: str, *, now_epoch: int | None = None) -> str:
    """Compatibility wrapper returning only the validated object key."""
    return verify_object_token_claims(token, now_epoch=now_epoch).object_key


_url_cache: RedisJsonCache | None = None


def _get_url_cache() -> RedisJsonCache | None:
    global _url_cache
    if _url_cache is None:
        try:
            _url_cache = RedisJsonCache(
                settings.redis_url,
                key_prefix="datastore:fileurl",
                # Expire the cache entry a minute before the URL itself so we never
                # hand back an about-to-expire URL.
                ttl_seconds=max(
                    60, datastore_settings.datastore_file_url_expiry_seconds - 60
                ),
            )
        except Exception:
            logger.warning("datastore.file_url.file_url_cache_unavailable_s.degraded")
            return None
    return _url_cache


async def build_object_url(
    storage: DatastoreStoragePort,
    object_key: str,
    *,
    expires_seconds: int | None = None,
    content_sha256: str | None = None,
) -> tuple[str, datetime]:
    """Build a short-lived URL for an arbitrary datastore object key."""
    expires_seconds = (
        expires_seconds or datastore_settings.datastore_file_url_expiry_seconds
    )
    cache = _get_url_cache()
    cache_key = f"{object_key}:{content_sha256 or ''}"

    if cache is not None:
        try:
            cached = await cache.get_raw(cache_key)
            if cached:
                payload = json.loads(cached)
                return payload["url"], datetime.fromisoformat(payload["expires_at"])
        except Exception:
            logger.debug("datastore.file_url.file_url_cache_read_s.observed")

    now = int(time.time())
    expires_at_epoch = now + expires_seconds
    expires_at = datetime.fromtimestamp(expires_at_epoch, tz=timezone.utc)

    if storage_supports_native_signed_urls():
        expires_hours = max(1, round(expires_seconds / 3600))
        url = await storage.get_signed_url(object_key, expires_hours=expires_hours)
    else:
        token = mint_object_token(
            object_key=object_key,
            expires_at_epoch=expires_at_epoch,
            content_sha256=content_sha256,
        )
        base = settings.api_url.rstrip("/")
        url = f"{base}{PUBLIC_FILE_PATH}?token={quote(token)}"

    if cache is not None:
        try:
            await cache.set_raw(
                cache_key,
                json.dumps({"url": url, "expires_at": expires_at.isoformat()}),
            )
        except Exception:
            logger.debug("datastore.file_url.file_url_cache_write_s.observed")

    return url, expires_at


async def build_file_url(
    storage: DatastoreStoragePort,
    entity: DatastoreFileEntity,
    *,
    expires_seconds: int | None = None,
) -> tuple[str, datetime]:
    """Short-lived URL for a file entity's original bytes."""
    key = datastore_storage_key(entity)
    return await build_object_url(
        storage,
        key,
        expires_seconds=expires_seconds,
        content_sha256=entity.content_sha256,
    )


def build_file_app_url(pod_id: UUID, path: str) -> str:
    """Authenticated frontend deep-link to a file (requires login to open).

    Permanent (no expiry): the frontend renders the file for any pod member who
    is signed in. ``path`` is the stored entity path, e.g.
    ``/pod/<id>/files?file=%2FKNOWLEDGE%2Fguide.md``.
    """
    base = settings.frontend_url.rstrip("/")
    return f"{base}/pod/{pod_id}/files?file={quote(path)}"
