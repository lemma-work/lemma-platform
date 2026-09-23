"""How much memory and CPU a Docker sandbox is given.

A workspace gets what its owner's plan pays for when the plan names a size, and
the configured default otherwise. A function sandbox always gets its own
configured envelope: plans size the workspace a person works in, not the
runtime a function runs on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.workspace.providers.base import ProviderCreateSpec
    from app.modules.workspace.providers.docker import DockerProviderConfig


def memory_bytes(
    config: DockerProviderConfig, spec: ProviderCreateSpec, *, is_function: bool
) -> int:
    """A workspace's memory is its plan's when the plan names one."""
    if is_function:
        return config.function_memory_bytes
    if spec.size is not None:
        return spec.size.memory_mb * 1024 * 1024
    return config.memory_bytes


def nano_cpus(
    config: DockerProviderConfig, spec: ProviderCreateSpec, *, is_function: bool
) -> int:
    """A workspace's CPUs are its plan's when the plan names them."""
    if is_function:
        return config.function_nano_cpus
    if spec.size is not None:
        return spec.size.cpu_count * 1_000_000_000
    return config.nano_cpus
