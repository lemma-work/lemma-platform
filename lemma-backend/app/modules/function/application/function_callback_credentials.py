"""Deterministic, non-persisted capabilities for function run callbacks."""

from __future__ import annotations

import base64
import hashlib
import hmac
from uuid import UUID


class FunctionCallbackCredentialSigner:
    """Derive a restart-stable callback capability without storing plaintext.

    The delegated function session authenticates invocation. This separate
    capability cannot authorize execution; it is supplied to the resident
    runtime for exact-run cancellation and must match the value returned after
    claim before artifact reads or terminal callbacks are accepted.
    """

    def __init__(self, secret: str) -> None:
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("function runtime credential secret must be 32 bytes")
        self._secret = secret.encode("utf-8")

    def derive(self, run_id: UUID) -> str:
        message = f"lemma-function-run-callback/v1/{run_id}".encode()
        digest = hmac.new(self._secret, message, hashlib.sha256).digest()
        encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return f"fcb_{encoded}"
