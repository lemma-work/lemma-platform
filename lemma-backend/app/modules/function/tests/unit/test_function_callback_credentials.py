from uuid import uuid4

import pytest

from app.modules.function.application.function_callback_credentials import (
    FunctionCallbackCredentialSigner,
)


def test_callback_credential_is_restart_stable() -> None:
    signer = FunctionCallbackCredentialSigner("s" * 32)
    run_id = uuid4()

    callback = signer.derive(run_id)

    assert callback == signer.derive(run_id)
    assert callback.startswith("fcb_")
    assert len(callback) == 47


def test_callback_credentials_require_a_real_secret() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        FunctionCallbackCredentialSigner("development")
