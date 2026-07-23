"""Deterministic, non-persisted credentials for one function attempt."""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Literal
from uuid import UUID


CredentialPurpose = Literal["ticket", "runtime"]


class FunctionAttemptCredentialSigner:
    """Derive restart-stable credentials without storing their plaintext.

    The attempt UUID is random and the purpose is domain separated. Re-claiming
    an already reserved attempt after a dispatcher restart therefore produces
    exactly the credential whose digest is stored in Postgres.
    """

    def __init__(self, secret: str) -> None:
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("function runtime credential secret must be 32 bytes")
        self._secret = secret.encode("utf-8")

    def derive(self, attempt_id: UUID, purpose: CredentialPurpose) -> str:
        message = f"lemma-function-attempt/v1/{purpose}/{attempt_id}".encode()
        digest = hmac.new(self._secret, message, hashlib.sha256).digest()
        encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        prefix = "fat" if purpose == "ticket" else "far"
        return f"{prefix}_{encoded}"

    @staticmethod
    def digest(credential: str) -> str:
        return hashlib.sha256(credential.encode("utf-8")).hexdigest()
