from __future__ import annotations

import os

import pytest

# AgentBox config is required to construct workspace/function services. Default
# to test-only values (set before app.core.config is imported) so suites run
# without a configured local AgentBox manager; workspace-marked e2e tests
# override these with a real local manager via their fixtures.
os.environ.setdefault("AGENTBOX_API_KEY", "test-agentbox-key")
os.environ.setdefault("AGENTBOX_API_URL", "http://localhost:9999")

WORKSPACE_FIXTURES = {
    "backend_server",
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
    """True when e2e uses the real Docker AgentBox; default is the fake."""
    mode = os.getenv("E2E_SANDBOX_MODE", "").lower()
    if mode == "docker":
        return True
    if mode == "fake":
        return False
    return os.getenv("E2E_REAL", "").lower() in ("1", "true", "yes")


def pytest_collection_modifyitems(config, items):
    """Classify e2e tests and gate them by the active e2e mode.

    Default mode is fast/mocked: the agent LLM is a deterministic FunctionModel
    and workspace tools hit the in-process fake AgentBox — so provider/agent-run
    tests RUN (no key, no Docker). Real mode (``E2E_REAL=1`` / ``E2E_LLM_MODE=real``
    / ``E2E_SANDBOX_MODE=docker``) hits the real model + Docker AgentBox.
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
        # Tests that need the real Docker AgentBox (workspace fixtures) or are
        # explicitly real-sandbox-only: skip unless running in real sandbox mode.
        if ("workspace" in marker_names or "real_sandbox" in marker_names) and (
            not real_sandbox
        ):
            item.add_marker(
                pytest.mark.skip(
                    reason="needs the real Docker AgentBox — set E2E_REAL=1 "
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


def pytest_sessionstart(session: pytest.Session) -> None:
    del session
    from app.modules.test_support import e2e_base

    e2e_base._cleanup_e2e_workspace_containers()


def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> None:
    del nextitem
    if "workspace" not in {marker.name for marker in item.iter_markers()}:
        return
    from app.modules.test_support import e2e_base

    e2e_base._cleanup_e2e_workspace_containers()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    del session, exitstatus
    from app.modules.test_support import e2e_base

    e2e_base._cleanup_e2e_workspace_containers()
    e2e_base._close_shared_contexts()
