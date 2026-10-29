"""A machine is built at the size on its row; a function keeps its own."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest


from app.core.ports.plan_limits import SandboxSize
from app.modules.workspace.domain.sandbox import (
    Sandbox,
    SandboxDesiredState,
    SandboxKind,
    SandboxOwnerKind,
)
from app.modules.workspace.providers.base import ProviderCreateSpec
from app.modules.workspace.providers.docker import DockerProviderConfig
from app.modules.workspace.providers.docker_sizing import memory_bytes, nano_cpus
from app.modules.workspace.config import WorkspaceSettings
from app.modules.workspace.providers import e2b_config
from app.modules.workspace.providers.e2b_config import E2BProviderConfig, template_for
from app.modules.workspace.services.sandbox_sizing import machine_size_for

PLUS = SandboxSize(cpu_count=4, memory_mb=8192)


def _spec(
    *, kind: SandboxKind = SandboxKind.WORKSPACE, size: SandboxSize | None = None
) -> ProviderCreateSpec:
    return ProviderCreateSpec(
        sandbox_id=uuid4(),
        kind=kind,
        epoch=1,
        name="sandbox",
        image="image",
        profile_name="profile",
        profile_digest="sha256:" + "a" * 64,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        size=size,
    )


def _e2b(**sized: str) -> E2BProviderConfig:
    return E2BProviderConfig(
        api_key="key",
        workspace_template="lemma-workspace",
        function_template="lemma-function",
        metadata_namespace="tests",
        workspace_size_templates=sized,
    )


# --- Docker -----------------------------------------------------------------


def test_docker_gives_a_workspace_its_plans_size():
    config = DockerProviderConfig()
    spec = _spec(size=PLUS)

    assert memory_bytes(config, spec, is_function=False) == 8192 * 1024 * 1024
    assert nano_cpus(config, spec, is_function=False) == 4_000_000_000


def test_docker_gives_an_unsized_workspace_the_configured_default():
    config = DockerProviderConfig()
    spec = _spec()

    assert memory_bytes(config, spec, is_function=False) == config.memory_bytes
    assert nano_cpus(config, spec, is_function=False) == config.nano_cpus


def test_docker_never_sizes_a_function_by_plan():
    config = DockerProviderConfig()
    spec = _spec(kind=SandboxKind.FUNCTION, size=PLUS)

    assert memory_bytes(config, spec, is_function=True) == config.function_memory_bytes
    assert nano_cpus(config, spec, is_function=True) == config.function_nano_cpus


# --- E2B ----------------------------------------------------------------------


def test_e2b_builds_a_sized_workspace_from_that_sizes_template():
    config = _e2b(**{"4x8192": "lemma-workspace-4x8192"})

    assert template_for(config, _spec(size=PLUS)) == "lemma-workspace-4x8192"


def test_e2b_serves_a_size_nobody_built_at_the_default_rather_than_refusing():
    assert template_for(_e2b(), _spec(size=PLUS)) == "lemma-workspace"


class _Warnings:
    def __init__(self) -> None:
        self.events: list[str] = []

    def warning(self, event: str, **_fields: object) -> None:
        self.events.append(event)


def test_e2b_serves_the_default_templates_own_size_from_it_without_a_warning(
    monkeypatch: pytest.MonkeyPatch,
):
    """1x2048 is what the default template is built at, so a plan selling it
    has a template: it was logged as missing one on every workspace create."""
    warnings = _Warnings()
    monkeypatch.setattr(e2b_config, "logger", warnings)
    small = SandboxSize(cpu_count=1, memory_mb=2048)

    assert template_for(_e2b(), _spec(size=small)) == "lemma-workspace"
    assert warnings.events == []

    template_for(_e2b(), _spec(size=PLUS))
    assert warnings.events == ["workspace.e2b.size_template_missing.degraded"]


def test_e2b_prefers_a_template_built_for_the_default_size_when_one_is_named():
    config = _e2b(**{"1x2048": "lemma-workspace-1x2048"})

    assert template_for(config, _spec(size=SandboxSize(1, 2048))) == (
        "lemma-workspace-1x2048"
    )


def test_the_default_template_size_is_what_the_builder_builds_by_default():
    """The backend's idea of the default template's size and the builder's
    defaults are two copies of one fact; this holds them together without
    importing the builder, which needs the optional E2B SDK."""
    builder = (
        Path(__file__).resolve().parents[5]
        / "sandbox-images"
        / "templates"
        / "e2b"
        / "build_templates.py"
    )
    constants = {
        node.targets[0].id: node.value.value
        for node in ast.parse(builder.read_text()).body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
    }
    built = f"{constants['DEFAULT_CPU_COUNT']}x{constants['DEFAULT_MEMORY_MB']}"

    assert e2b_config.DEFAULT_WORKSPACE_TEMPLATE_SIZE == built
    assert WorkspaceSettings.model_fields["e2b_workspace_template_size"].default == (
        built
    )


def test_e2b_builds_a_function_from_the_function_template_whatever_the_size():
    config = _e2b(**{"4x8192": "lemma-workspace-4x8192"})

    assert template_for(config, _spec(kind=SandboxKind.FUNCTION, size=PLUS)) == (
        "lemma-function"
    )


# --- which size a row is built at -----------------------------------------------


def _sandbox(*, kind: SandboxKind, size: str) -> Sandbox:
    return Sandbox(
        id=uuid4(),
        kind=kind,
        owner_kind=SandboxOwnerKind.USER
        if kind is SandboxKind.WORKSPACE
        else SandboxOwnerKind.POD,
        owner_id=uuid4(),
        slug="default",
        display_name="workspace",
        profile_name="profile",
        profile_digest="sha256:" + "a" * 64,
        desired_state=SandboxDesiredState.PRESENT,
        epoch=1,
        storage_generation=1,
        size=size,
    )


def test_a_machine_is_built_at_the_size_on_its_row():
    sandbox = _sandbox(kind=SandboxKind.WORKSPACE, size="4x8")

    assert machine_size_for(sandbox) == PLUS


def test_a_machine_still_at_the_legacy_size_keeps_the_configured_size():
    sandbox = _sandbox(kind=SandboxKind.WORKSPACE, size="1x2")

    assert machine_size_for(sandbox) is None


def test_a_function_sandbox_keeps_the_configured_size():
    sandbox = _sandbox(kind=SandboxKind.FUNCTION, size="4x8")

    assert machine_size_for(sandbox) is None


def test_e2b_builds_a_docker_machine_from_the_docker_template():
    config = _e2b()
    spec = replace(_spec(size=PLUS), template="workspace-docker")

    assert template_for(config, spec) == "lemma-workspace-docker"
