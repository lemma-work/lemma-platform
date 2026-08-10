from __future__ import annotations

import os

import pytest

# Tests are deterministic and self-contained: they set the config they need (via
# fixtures / the defaults below / monkeypatch), NOT a developer's local ``.env``.
# A leaked ``.env`` makes the suite non-deterministic — a value present locally
# but absent in CI (e.g. ``API_URL``, ``ENABLE_TELEGRAM_POLLING_MODE``) silently
# flips tests. So disable ``.env`` loading for the whole suite (matching CI, which
# has no ``.env``). It is still honored in real-LLM / real-sandbox mode, where the
# ``.env`` legitimately supplies real API keys. This MUST run before any
# ``app.core.config`` import (env_file is resolved at Settings-class definition).
_REAL_MODE = (
    os.getenv("E2E_REAL", "").lower() in ("1", "true", "yes")
    or os.getenv("E2E_LLM_MODE", "").lower() == "real"
    or os.getenv("E2E_SANDBOX_MODE", "").lower() in {"docker", "e2b"}
    or os.getenv("LEMMA_RUN_PROVIDER_E2E") == "1"
)
if not _REAL_MODE:
    os.environ.setdefault("LEMMA_DISABLE_DOTENV", "1")

WORKSPACE_FIXTURES = {
    "configure_workspace_api_url",
    "workspace_image",
    "_configure_function_workspace_api_url",
}

AGENT_PROVIDER_TESTS = {
    "test_file_creation_tool_call_streams_tool_json_tokens",
    "test_stopping_streaming_agent_run_does_not_wedge_worker",
    "test_task_conversation_waits_then_completes_with_real_worker_model",
    "test_pod_agent_http_lifecycle_with_real_worker_model",
    "test_pod_assistant_http_lifecycle_with_real_worker_model",
    "test_first_run_generates_title_with_real_worker_model",
    "test_agent_tool_http_apis",
}


def _e2e_real_llm() -> bool:
    """True when e2e hits the real model (needs a key); default is the mock."""
    mode = os.getenv("E2E_LLM_MODE", "").lower()
    if mode == "real":
        return True
    if mode == "mock":
        return False
    return (
        os.getenv("E2E_REAL", "").lower() in ("1", "true", "yes")
        or os.getenv("LEMMA_RUN_PROVIDER_E2E") == "1"
    )


def _e2e_real_sandbox() -> bool:
    """All supported E2E sandbox modes use a real the sandbox runtime provider."""
    mode = os.getenv("E2E_SANDBOX_MODE", "").lower()
    return mode in {"", "docker", "e2b"}


def pytest_collection_modifyitems(config, items):
    """Classify e2e tests and gate them by the active e2e mode.

    The LLM may be deterministic, but sandbox behavior always uses a real
    Docker or credential-gated E2B the sandbox runtime.
    """
    real_llm = _e2e_real_llm()
    real_sandbox = _e2e_real_sandbox()

    key_available = True
    if real_llm:
        from app.modules.agent.tests.e2e.system_lemma_helpers import (
            system_lemma_api_key,
        )

        key_available = bool(system_lemma_api_key())

    for item in items:
        path_parts = set(item.path.parts)
        if {"tests", "e2e"}.issubset(path_parts):
            item.add_marker(pytest.mark.e2e)
        fixture_names = set(getattr(item, "fixturenames", ()))
        if "worker" in fixture_names:
            item.add_marker(pytest.mark.worker)
        if fixture_names & WORKSPACE_FIXTURES:
            item.add_marker(pytest.mark.workspace)
        # Any test that (transitively) needs the shared Kreuzberg container asks
        # for the ``kreuzberg_url`` fixture — directly, or via ``kreuzberg_wired``
        # / ``index_datastore_file``. Mark those ``indexing`` so test-e2e-fast can
        # exclude them and never boot the RAM-heavy extraction container; they run
        # in the dedicated test-e2e-indexing job instead.
        if "kreuzberg_url" in fixture_names:
            item.add_marker(pytest.mark.indexing)
        if (
            (
                item.path.name == "test_agent_usage_e2e.py"
                and item.originalname
                == "test_agent_run_records_usage_and_usage_apis_filter_it"
            )
            or (
                item.path.name == "test_agent_e2e.py"
                and item.originalname in AGENT_PROVIDER_TESTS
            )
        ):
            item.add_marker(pytest.mark.provider)

        marker_names = {marker.name for marker in item.iter_markers()}
        # Tests that need the real Docker sandbox (workspace fixtures) or are
        # explicitly real-sandbox-only: skip unless running in real sandbox mode.
        if ("workspace" in marker_names or "real_sandbox" in marker_names) and (
            not real_sandbox
        ):
            item.add_marker(
                pytest.mark.skip(
                    reason="needs the real Docker sandbox — set E2E_REAL=1 "
                    "(or E2E_SANDBOX_MODE=docker)."
                )
            )
            continue
        # Tests that only make sense against a real model: skip unless real LLM.
        if "real_llm" in marker_names and not real_llm:
            item.add_marker(
                pytest.mark.skip(
                    reason="needs the real model — set E2E_REAL=1 (or E2E_LLM_MODE=real)."
                )
            )
            continue
        # Provider/agent-run tests run under the mock by default; in real LLM
        # mode they need a configured key.
        if "provider" in marker_names and real_llm and not key_available:
            item.add_marker(
                pytest.mark.skip(
                    reason="real LLM mode but LEMMA_OPENAI_API_KEY is not configured."
                )
            )


@pytest.fixture(scope="session", autouse=True)
def _e2e_llm_mode_baseline() -> str:
    """The LLM mode before any e2e bootstrap has run.

    Autouse and session-scoped, so it is resolved before ``e2e_settings`` - which
    is requested by other fixtures rather than autouse - can mutate it.
    """
    from app.core.config import settings

    return settings.e2e_llm_mode


@pytest.fixture(autouse=True)
def _isolate_e2e_llm_mode(request, _e2e_llm_mode_baseline: str):
    """Keep e2e's mock model out of every test that did not ask for it.

    The e2e bootstrap sets ``settings.e2e_llm_mode`` process-wide (it has to:
    the worker subprocess inherits the mode through ``os.environ``) and never
    restores it. ``is_mock_llm_enabled`` reads that setting, and
    ``pydantic_ai_model_from_runtime_profile`` short-circuits to the
    deterministic FunctionModel when it is true - so a unit test that builds a
    model and merely happens to run after an e2e test in the same process gets
    the mock instead of the real provider model, and fails on a type assertion
    that has nothing to do with what it is testing.

    ``tests/e2e`` sorts before ``tests/unit``, so ``pytest app/modules/agent``
    hits this while CI does not, because CI runs the two suites separately
    (``pytest -m "not e2e"``). That asymmetry is what makes it expensive: it
    only ever bites someone running a module locally, and it looks like a real
    regression in whatever they were working on.

    ``e2e_sandbox_mode`` leaks the same way but is read by no production code,
    so it needs no equivalent guard.
    """
    from app.core.config import settings

    if "e2e" in request.keywords:
        yield
        return

    previous = settings.e2e_llm_mode
    settings.e2e_llm_mode = _e2e_llm_mode_baseline
    try:
        yield
    finally:
        settings.e2e_llm_mode = previous


@pytest.fixture(autouse=True)
def _isolate_shared_redis_clients():
    """Keep the process-wide Redis client registry from leaking across tests.

    The registry is deliberately process-wide in production. In tests that
    makes it shared mutable state: a test that patches ``Redis.from_url`` (a
    class attribute, so the patch is global) would otherwise have its fake
    cached in the registry for the rest of the session, because monkeypatch
    restores the constructor but knows nothing about the cache.

    Clearing rather than closing is intentional - closing real pooled clients
    between every test would dominate suite runtime for no benefit.
    """
    from app.core.infrastructure.redis import client as redis_client

    redis_client._clients.clear()
    yield
    redis_client._clients.clear()


def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> None:
    del nextitem
    if "workspace" not in {marker.name for marker in item.iter_markers()}:
        return
    from app.modules.test_support import e2e_base

    # Per-test: reap only this test's sandbox pods, NOT the shared
    # session testcontainers (which carry the same lemma.e2e label).
    e2e_base._cleanup_e2e_workspace_containers(sandboxes_only=True)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    del session, exitstatus
    from app.modules.test_support import e2e_base

    # Context managers remove only the containers owned by this pytest process.
    # Never sweep every ``lemma.e2e=true`` container here: a separately invoked
    # pytest session may be running concurrently and uses the same shared label.
    e2e_base._close_shared_contexts()
