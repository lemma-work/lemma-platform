from uuid import uuid4

import pytest

from app.modules.function.application.function_runtime_credentials import (
    FunctionRuntimeCapabilitySigner,
)


def test_run_capability_is_restart_stable() -> None:
    signer = FunctionRuntimeCapabilitySigner("s" * 32)
    run_id = uuid4()

    callback = signer.derive(run_id)

    assert callback == signer.derive(run_id)
    assert callback.startswith("fcb_")
    assert len(callback) == 47


def test_runtime_capabilities_require_a_real_secret() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        FunctionRuntimeCapabilitySigner("development")


def test_compilation_credential_is_bound_to_function_and_revision() -> None:
    signer = FunctionRuntimeCapabilitySigner("c" * 32)
    function_id = uuid4()
    revision_hash = f"sha256:{'a' * 64}"

    credential = signer.derive_compilation(function_id, revision_hash)

    assert credential.startswith("fcc_")
    assert signer.verify_compilation(
        credential,
        function_id=function_id,
        revision_hash=revision_hash,
    )
    assert not signer.verify_compilation(
        credential,
        function_id=function_id,
        revision_hash=f"sha256:{'b' * 64}",
    )
