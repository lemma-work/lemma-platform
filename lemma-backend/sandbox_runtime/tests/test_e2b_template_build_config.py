from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "sandbox-images"
    / "templates"
    / "e2b"
    / "build_templates.py"
)
_SPEC = importlib.util.spec_from_file_location("e2b_build_templates", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_template_resources_default_to_one_cpu_and_two_gib(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENTBOX_E2B_WORKSPACE_CPU_COUNT", raising=False)
    monkeypatch.delenv("AGENTBOX_E2B_WORKSPACE_MEMORY_MB", raising=False)

    assert (
        _MODULE._positive_int_environment(
            "AGENTBOX_E2B_WORKSPACE_CPU_COUNT",
            default=_MODULE.DEFAULT_CPU_COUNT,
        )
        == 1
    )
    assert (
        _MODULE._positive_int_environment(
            "AGENTBOX_E2B_WORKSPACE_MEMORY_MB",
            default=_MODULE.DEFAULT_MEMORY_MB,
        )
        == 2048
    )


def test_template_resources_accept_positive_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTBOX_E2B_FUNCTION_CPU_COUNT", "2")

    assert (
        _MODULE._positive_int_environment(
            "AGENTBOX_E2B_FUNCTION_CPU_COUNT",
            default=_MODULE.DEFAULT_CPU_COUNT,
        )
        == 2
    )


@pytest.mark.parametrize("value", ["0", "-1", "not-an-integer"])
def test_template_resources_reject_invalid_environment(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("AGENTBOX_E2B_FUNCTION_CPU_COUNT", value)

    with pytest.raises(ValueError):
        _MODULE._positive_int_environment(
            "AGENTBOX_E2B_FUNCTION_CPU_COUNT",
            default=_MODULE.DEFAULT_CPU_COUNT,
        )
