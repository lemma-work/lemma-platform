"""Key-material resolution for the crypto facility.

Centralizes how raw key bytes are discovered from configuration so the static
provider and the legacy-decryption fallback agree on exactly which keys exist.

Resolution order for a single primary key (when no explicit keyset is given):

1. ``SECRET_ENCRYPTION_KEY`` (the new, system-wide name)
2. ``CONNECTOR_ENCRYPTION_KEY`` (legacy name — kept for back-compat)
3. the deterministic local/testing fallback (``sha256`` of a fixed dev seed) —
   ONLY when ``LEMMA_ALLOW_LOCAL_FALLBACK_KEY`` is set (explicit opt-in). The
   seed is a public repo constant, so using it outside local/testing would let
   anyone with the repo decrypt secrets at rest.

A multi-key **keyset** (``SECRET_ENCRYPTION_KEYSET``) enables rotation: a JSON
array of ``{"kid","key","primary"}`` objects.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.crypto.ports import KeyMaterial, Keyring
from app.core.log.log import get_logger

logger = get_logger(__name__)

#: Matches the historical FernetSecretEncryptionAdapter dev fallback so local
#: data written before this module keeps decrypting with no configuration.
#: Only consulted when :data:`ALLOW_LOCAL_FALLBACK_ENV` is set — never in the
#: default path.
LOCAL_KEY_SEED = b"lemma-local-connector-secret-key"
LEGACY_ENV_VAR = "CONNECTOR_ENCRYPTION_KEY"
#: Opt-in env flag that authorises use of :func:`local_fallback_secret`. The
#: deterministic seed is public; the operator must explicitly say "yes, I know
#: this is a dev key" before it is used to encrypt or decrypt production data.
ALLOW_LOCAL_FALLBACK_ENV = "LEMMA_ALLOW_LOCAL_FALLBACK_KEY"


def local_fallback_secret() -> bytes:
    """Deterministic Fernet key used in local/testing when nothing is configured.

    This key is derived from a constant seed that is checked into the repo, so
    it MUST NOT be used to protect real data. Callers must gate its use behind
    :func:`_local_fallback_allowed` (or equivalent explicit opt-in).
    """
    digest = hashlib.sha256(LOCAL_KEY_SEED).digest()
    return base64.urlsafe_b64encode(digest)


def _local_fallback_allowed() -> bool:
    """True when the operator has explicitly opted in to the deterministic seed.

    Reads from the typed ``Settings`` field first (so tests that
    ``monkeypatch.setattr(settings, ...)`` keep working), then from the raw env
    as a safety net for callers that bypass the Settings cache.
    """
    raw = getattr(settings, "allow_local_fallback_key", None)
    if isinstance(raw, bool):
        if raw:
            return True
    elif isinstance(raw, str) and raw.strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return os.environ.get(ALLOW_LOCAL_FALLBACK_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def derive_kid(secret: bytes) -> str:
    """Stable short key id derived from the key bytes (for single-key configs)."""
    return "s" + hashlib.sha256(secret).hexdigest()[:8]


def is_valid_fernet_key(secret: bytes) -> bool:
    try:
        Fernet(secret)
        return True
    except (ValueError, TypeError):
        return False


def _single_primary_secret() -> bytes | None:
    configured = settings.secret_encryption_key or os.environ.get(LEGACY_ENV_VAR)
    if configured:
        return configured.encode("utf-8")
    if settings.is_local_mode() and _local_fallback_allowed():
        return local_fallback_secret()
    return None


def load_static_keyring() -> Keyring:
    """Build the static keyring from configuration.

    Raises ``RuntimeError`` when no key is configured and the operator has not
    explicitly opted in to the deterministic local seed. Encrypting credentials
    at rest must never silently fall back to a known key — local/testing now
    requires ``LEMMA_ALLOW_LOCAL_FALLBACK_KEY=1`` (the seed is public), and any
    non-local environment requires ``SECRET_ENCRYPTION_KEY`` or
    ``SECRET_ENCRYPTION_KEYSET``.
    """
    raw_keyset = (settings.secret_encryption_keyset or "").strip()
    if raw_keyset:
        return parse_keyset(raw_keyset)

    secret = _single_primary_secret()
    if secret is None:
        raise RuntimeError(
            "No secret encryption key configured. Set SECRET_ENCRYPTION_KEY "
            "(or SECRET_ENCRYPTION_KEYSET). For local/testing you may also set "
            "LEMMA_ALLOW_LOCAL_FALLBACK_KEY=1 to use the (public, repo-derived) "
            "dev seed — never do this for real data."
        )
    if not is_valid_fernet_key(secret):
        raise RuntimeError(
            "SECRET_ENCRYPTION_KEY is not a valid Fernet key (expected a 44-char "
            "urlsafe-base64 32-byte key; generate one with Fernet.generate_key())."
        )
    kid = derive_kid(secret)
    return Keyring(primary_kid=kid, keys={kid: KeyMaterial(kid=kid, secret=secret)})


def parse_keyset(raw_keyset: str) -> Keyring:
    """Parse a JSON keyset (``[{"kid","key","primary"}]``) into a :class:`Keyring`.

    Shared by the static provider (env config) and the Secret Manager provider
    (secret payload) so both agree on format and validation.
    """
    try:
        entries = json.loads(raw_keyset)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Secret keyset is not valid JSON: {exc}") from exc
    if not isinstance(entries, list) or not entries:
        raise RuntimeError("Secret keyset must be a non-empty JSON array")

    keys: dict[str, KeyMaterial] = {}
    primary_kid: str | None = None
    for entry in entries:
        if not isinstance(entry, dict) or "kid" not in entry or "key" not in entry:
            raise RuntimeError("Each secret keyset entry needs 'kid' and 'key' fields")
        kid = str(entry["kid"])
        secret = str(entry["key"]).encode("utf-8")
        if not is_valid_fernet_key(secret):
            raise RuntimeError(f"Secret keyset key {kid!r} is not a valid Fernet key")
        keys[kid] = KeyMaterial(kid=kid, secret=secret)
        if entry.get("primary"):
            if primary_kid is not None:
                raise RuntimeError("Secret keyset marks more than one primary key")
            primary_kid = kid

    if primary_kid is None:
        # Default the primary to the first entry when none is flagged.
        primary_kid = next(iter(keys))
    return Keyring(primary_kid=primary_kid, keys=keys)


def legacy_candidate_secrets() -> list[bytes]:
    """All keys that might decrypt legacy ``fernet-json-v1`` blobs.

    The union of every configured key (keyset + single + legacy env var + local
    fallback), deduped and filtered to valid Fernet keys. Used to build the
    backward-compatible v1 decryptor regardless of the active provider.
    """
    candidates: list[bytes] = []

    def add(secret: bytes | None) -> None:
        if secret and secret not in candidates and is_valid_fernet_key(secret):
            candidates.append(secret)

    raw_keyset = (settings.secret_encryption_keyset or "").strip()
    if raw_keyset:
        try:
            for material in parse_keyset(raw_keyset).keys.values():
                add(material.secret)
        except RuntimeError as exc:
            logger.warning("Ignoring unparsable SECRET_ENCRYPTION_KEYSET for legacy decrypt: %s", exc)

    if settings.secret_encryption_key:
        add(settings.secret_encryption_key.encode("utf-8"))
    legacy_env = os.environ.get(LEGACY_ENV_VAR)
    if legacy_env:
        add(legacy_env.encode("utf-8"))
    if settings.is_local_mode() and _local_fallback_allowed():
        add(local_fallback_secret())

    return candidates
