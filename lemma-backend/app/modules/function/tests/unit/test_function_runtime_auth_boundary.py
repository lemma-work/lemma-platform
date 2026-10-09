from app.core.auth_exemptions import exemption_of


def test_all_function_runtime_backend_routes_use_global_auth() -> None:
    paths = (
        (
            "/internal/function-runtime/functions/"
            "019ba7e8-5115-7000-8000-000000000002/artifacts/"
            f"sha256:{'a' * 64}"
        ),
        "/internal/function-runtime/runs/019ba7e8-5115-7000-8000-000000000001:terminal",
    )
    assert all(exemption_of(path, "POST") is None for path in paths)
    assert all(exemption_of(path, "GET") is None for path in paths)
