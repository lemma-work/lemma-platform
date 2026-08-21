from app.core.security import EXCLUDED_PATHS


def test_all_function_runtime_backend_routes_use_global_auth() -> None:
    paths = (
        "/internal/function-runtime/functions/"
        "019ba7e8-5115-7000-8000-000000000002/artifacts/"
        f"sha256:{'a' * 64}",
        "/internal/function-runtime/runs/019ba7e8-5115-7000-8000-000000000001:terminal",
    )
    assert all(not path.startswith(EXCLUDED_PATHS) for path in paths)
