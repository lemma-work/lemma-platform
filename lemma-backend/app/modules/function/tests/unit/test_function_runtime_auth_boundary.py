from app.core.security import _is_function_runtime_callback_path


def test_function_session_claim_uses_global_auth_but_callbacks_do_not() -> None:
    assert not _is_function_runtime_callback_path(
        "/internal/function-runtime/runs/"
        "019ba7e8-5115-7000-8000-000000000001:claim"
    )
    assert _is_function_runtime_callback_path(
        "/internal/function-runtime/runs/"
        "019ba7e8-5115-7000-8000-000000000001/artifact"
    )
    assert _is_function_runtime_callback_path(
        "/internal/function-runtime/runs/"
        "019ba7e8-5115-7000-8000-000000000001:terminal"
    )
    assert _is_function_runtime_callback_path(
        "/internal/function-runtime/functions/"
        "019ba7e8-5115-7000-8000-000000000002/artifact"
    )
    assert not _is_function_runtime_callback_path(
        "/internal/function-runtime/functions/not-a-uuid/artifact"
    )
