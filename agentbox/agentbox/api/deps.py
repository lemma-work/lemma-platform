from __future__ import annotations

from starlette.requests import HTTPConnection

from agentbox.lifecycle_manager import SandboxLifecycleManager
from agentbox.providers import SandboxProvider
from agentbox.state_store.protocol import AsyncStateStore


def sandbox_provider(connection: HTTPConnection) -> SandboxProvider:
    return connection.app.state.sandbox_provider


def state_store(connection: HTTPConnection) -> AsyncStateStore:
    return connection.app.state.store


def lifecycle_manager(connection: HTTPConnection) -> SandboxLifecycleManager:
    return connection.app.state.lifecycle_manager
