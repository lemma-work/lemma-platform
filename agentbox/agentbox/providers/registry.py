from __future__ import annotations

from importlib import import_module, metadata
from typing import Callable

from .protocol import SandboxLifecycleProvider


ProviderFactory = Callable[[], SandboxLifecycleProvider]

_BUILTIN_PROVIDERS = {
    "docker": "agentbox.providers.docker:DockerSandboxProvider",
    "podman": "agentbox.providers.podman:PodmanSandboxProvider",
    "kubernetes": "agentbox.kubernetes:SandboxKubernetesClient",
    "e2b": "agentbox.providers.e2b:E2BSandboxProvider",
    "daytona": "agentbox.providers.daytona:DaytonaSandboxProvider",
}
_registered: dict[str, ProviderFactory] = {}


def register_provider(name: str, factory: ProviderFactory) -> None:
    normalized = name.strip().lower()
    if not normalized:
        raise ValueError("provider name cannot be empty")
    _registered[normalized] = factory


def _load_path(value: str) -> ProviderFactory:
    module_name, separator, attribute = value.partition(":")
    if not separator:
        raise ValueError(f"invalid provider import path: {value}")
    factory = getattr(import_module(module_name), attribute)
    if not callable(factory):
        raise TypeError(f"provider factory {value} is not callable")
    return factory


def _entrypoint_factory(name: str) -> ProviderFactory | None:
    matches = metadata.entry_points().select(group="agentbox.providers", name=name)
    entrypoint = next(iter(matches), None)
    if entrypoint is None:
        return None
    factory = entrypoint.load()
    if not callable(factory):
        raise TypeError(f"provider entry point {name} is not callable")
    return factory


def build_provider(name: str) -> SandboxLifecycleProvider:
    selected = name.strip().lower()
    factory = _registered.get(selected)
    if factory is None and selected in _BUILTIN_PROVIDERS:
        factory = _load_path(_BUILTIN_PROVIDERS[selected])
    if factory is None:
        factory = _entrypoint_factory(selected)
    if factory is None and ":" in selected:
        factory = _load_path(selected)
    if factory is None:
        raise RuntimeError(f"Unsupported AgentBox provider: {selected}")
    return factory()


def provider_names() -> tuple[str, ...]:
    return tuple(sorted(set(_BUILTIN_PROVIDERS) | set(_registered)))
