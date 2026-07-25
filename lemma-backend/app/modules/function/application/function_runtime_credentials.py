"""Deterministic, non-persisted capabilities for the function runtime."""

from __future__ import annotations

import base64
import hashlib
import hmac
from uuid import UUID


class FunctionRuntimeCapabilitySigner:
    """Derive restart-stable runtime capabilities without storing plaintext.

    The delegated function session authenticates invocation. This separate
    capability family cannot authorize execution. Run-scoped credentials permit
    exact cancellation, artifact reads, and terminal callbacks after claim.
    Revision-scoped credentials permit only draft artifact schema inspection.
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

    def derive_compilation(self, function_id: UUID, revision_hash: str) -> str:
        """Authorize one immutable artifact inspection by the function runtime."""

        message = (
            f"lemma-function-definition-compile/v1/{function_id}/{revision_hash}"
        ).encode()
        digest = hmac.new(self._secret, message, hashlib.sha256).digest()
        encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return f"fcc_{encoded}"

    def verify_compilation(
        self,
        credential: str,
        *,
        function_id: UUID,
        revision_hash: str,
    ) -> bool:
        return hmac.compare_digest(
            credential,
            self.derive_compilation(function_id, revision_hash),
        )
