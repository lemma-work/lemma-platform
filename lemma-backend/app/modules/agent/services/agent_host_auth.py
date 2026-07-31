"""Pairing codes and host credentials for Agent Host.

An Agent Host authenticates with a single opaque secret issued once at
pairing time. The server stores only the SHA-256 hash, so a database leak
exposes no usable credentials; the secret is rotatable by re-pairing and
revocable per host.

The secrets are 256-bit and drawn from ``secrets.token_urlsafe``, so an
unsalted hash is sufficient here: there is no low-entropy keyspace for a
rainbow table or brute-force search to cover.
"""

from __future__ import annotations

import hashlib
import secrets


class InvalidAgentHostCredential(ValueError):
    """A host credential is missing, malformed, or unknown."""


def generate_pairing_code() -> str:
    return secrets.token_urlsafe(32)


def pairing_code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def generate_host_secret() -> str:
    return secrets.token_urlsafe(32)


def host_secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()
