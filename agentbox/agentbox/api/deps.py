from __future__ import annotations

from starlette.requests import HTTPConnection

from agentbox.filesystem import FilesystemService
from agentbox.lifecycle import SandboxLifecycleService
from agentbox.port_access import PortAccessService
from agentbox.processes import ProcessExecutionService
from agentbox.python_sessions import PythonSessionService


def sandbox_lifecycle(connection: HTTPConnection) -> SandboxLifecycleService:
    return connection.app.state.sandbox_lifecycle


def process_execution(connection: HTTPConnection) -> ProcessExecutionService:
    return connection.app.state.process_execution


def filesystem(connection: HTTPConnection) -> FilesystemService:
    return connection.app.state.filesystem


def python_sessions(connection: HTTPConnection) -> PythonSessionService:
    return connection.app.state.python_sessions


def port_access(connection: HTTPConnection) -> PortAccessService:
    return connection.app.state.port_access
