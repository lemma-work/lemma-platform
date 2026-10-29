from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


# The builder these exercise imports the E2B SDK, which is an optional extra:
# a Docker-only deployment installs neither, and the unit job deliberately
# does not either, so that the provider's SDK seam stays honest. The E2B
# conformance workflow installs it and runs these.
pytest.importorskip("e2b")

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
    monkeypatch.delenv("E2B_WORKSPACE_CPU_COUNT", raising=False)
    monkeypatch.delenv("E2B_WORKSPACE_MEMORY_MB", raising=False)

    assert (
        _MODULE._positive_int_environment(
            "E2B_WORKSPACE_CPU_COUNT",
            default=_MODULE.DEFAULT_CPU_COUNT,
        )
        == 1
    )
    assert (
        _MODULE._positive_int_environment(
            "E2B_WORKSPACE_MEMORY_MB",
            default=_MODULE.DEFAULT_MEMORY_MB,
        )
        == 2048
    )


def test_template_resources_accept_positive_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E2B_FUNCTION_CPU_COUNT", "2")

    assert (
        _MODULE._positive_int_environment(
            "E2B_FUNCTION_CPU_COUNT",
            default=_MODULE.DEFAULT_CPU_COUNT,
        )
        == 2
    )


@pytest.mark.parametrize("value", ["0", "-1", "not-an-integer"])
def test_template_resources_reject_invalid_environment(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("E2B_FUNCTION_CPU_COUNT", value)

    with pytest.raises(ValueError):
        _MODULE._positive_int_environment(
            "E2B_FUNCTION_CPU_COUNT",
            default=_MODULE.DEFAULT_CPU_COUNT,
        )


def test_docker_template_defaults_to_four_cpus_and_eight_gib() -> None:
    assert (_MODULE.DEFAULT_DOCKER_CPU_COUNT, _MODULE.DEFAULT_DOCKER_MEMORY_MB) == (
        4,
        8192,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2x4096", (2, 4096)), ("4x8192", (4, 8192)), ("8x16384", (8, 16384))],
)
def test_sizes_parse_as_the_backend_size_key(raw: str, expected) -> None:
    assert _MODULE.parse_size(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "2", "2x", "x4096", "0x4096", "2x0", "2x4000", "ax4096"]
)
def test_sizes_reject_what_is_not_a_size(raw: str) -> None:
    with pytest.raises(ValueError):
        _MODULE.parse_size(raw)


@pytest.mark.parametrize(
    ("base", "size", "default", "suffix", "expected"),
    [
        # The default size keeps the bare name the backend defaults to.
        ("lemma-workspace-docker", None, (4, 8192), "", "lemma-workspace-docker"),
        ("lemma-workspace-docker", (4, 8192), (4, 8192), "", "lemma-workspace-docker"),
        # E2B_DOCKER_SIZE_TEMPLATES {"8x16384": "lemma-workspace-docker-8x16"}
        (
            "lemma-workspace-docker",
            (8, 16384),
            (4, 8192),
            "",
            "lemma-workspace-docker-8x16",
        ),
        # E2B_WORKSPACE_SIZE_TEMPLATES {"2x4096": "lemma-workspace-2x4"}
        ("lemma-workspace", (2, 4096), (1, 2048), "", "lemma-workspace-2x4"),
        # A candidate build's suffix goes last.
        (
            "lemma-workspace-docker",
            (8, 16384),
            (4, 8192),
            "-candidate",
            "lemma-workspace-docker-8x16-candidate",
        ),
    ],
)
def test_template_names_carry_a_non_default_size(
    base: str, size, default, suffix: str, expected: str
) -> None:
    assert (
        _MODULE.template_name(base, size=size, default_size=default, name_suffix=suffix)
        == expected
    )


def test_the_docker_target_reads_its_own_size_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("E2B_WORKSPACE_DOCKER_CPU_COUNT", raising=False)
    monkeypatch.setenv("E2B_WORKSPACE_DOCKER_MEMORY_MB", "16384")

    targets = _MODULE._targets()

    assert (
        set(targets)
        == set(_MODULE.TARGETS)
        == {
            "workspace",
            "workspace-docker",
            "function",
        }
    )
    factory, base, default_size = targets["workspace-docker"]
    assert factory is _MODULE.workspace_docker_template
    assert base == "lemma-workspace-docker"
    assert default_size == (4, 16384)


def test_a_size_needs_one_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("E2B_API_KEY", "unused")
    with pytest.raises(ValueError):
        _MODULE.build(target="all", size="2x4096")
