from uuid import uuid4

import pytest

from app.modules.function.application.function_attempt_credentials import (
    FunctionAttemptCredentialSigner,
)


def test_attempt_credentials_are_stable_and_domain_separated() -> None:
    signer = FunctionAttemptCredentialSigner("s" * 32)
    attempt_id = uuid4()

    ticket = signer.derive(attempt_id, "ticket")
    runtime = signer.derive(attempt_id, "runtime")

    assert ticket == signer.derive(attempt_id, "ticket")
    assert runtime == signer.derive(attempt_id, "runtime")
    assert ticket != runtime
    assert ticket.startswith("fat_")
    assert runtime.startswith("far_")
    assert len(signer.digest(ticket)) == 64


def test_attempt_credentials_require_a_real_secret() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        FunctionAttemptCredentialSigner("development")
