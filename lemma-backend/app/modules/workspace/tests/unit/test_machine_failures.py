"""The one table from a machine's runtime failure to an HTTP answer."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.api.exception_handlers import register_exception_handlers
from app.modules.workspace.api.controllers.machine_failures import (
    MACHINE_FAILURES,
    MachineFailureRoute,
    is_missing_path,
    machine_call_failed,
)
from app.modules.workspace.providers.runtime_errors import (
    WorkspaceRuntimeError,
    WorkspaceRuntimeFileConflict,
    WorkspaceRuntimeFileNotFound,
    WorkspaceRuntimeFileRejected,
    WorkspaceRuntimePathProtected,
    WorkspaceRuntimeProcessGone,
    WorkspaceRuntimeUnauthorized,
)
from sandbox_runtime.errors import (
    SandboxCapabilityUnsupported,
    SandboxError,
    SandboxNotFound,
    SandboxNotReady,
    SandboxPathConflict,
    SandboxPathNotFound,
    SandboxPathProtected,
    SandboxProcessNotFound,
    SandboxRejected,
    SandboxUnauthorized,
    SandboxUnavailable,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (SandboxPathNotFound("gone"), 404, "MACHINE_PATH_NOT_FOUND"),
        (WorkspaceRuntimeFileNotFound("gone"), 404, "MACHINE_PATH_NOT_FOUND"),
        (SandboxProcessNotFound("gone"), 404, "MACHINE_PROCESS_NOT_FOUND"),
        (WorkspaceRuntimeProcessGone("gone"), 404, "MACHINE_PROCESS_NOT_FOUND"),
        (SandboxPathConflict("there"), 409, "MACHINE_PATH_CONFLICT"),
        (WorkspaceRuntimeFileConflict("there"), 409, "MACHINE_PATH_CONFLICT"),
        (
            SandboxCapabilityUnsupported("docker", kind="connected"),
            409,
            "MACHINE_CAPABILITY_MISSING",
        ),
        (
            WorkspaceRuntimeFileRejected("big", status_code=413),
            413,
            "MACHINE_FILE_REJECTED",
        ),
        (SandboxRejected("big"), 413, "MACHINE_FILE_REJECTED"),
        # QA: a write through a link into the profile was refused, but answered
        # as a file too large. Both fabrics now say what it is.
        (SandboxPathProtected("profile"), 403, "MACHINE_PATH_PROTECTED"),
        (WorkspaceRuntimePathProtected("profile"), 403, "MACHINE_PATH_PROTECTED"),
        (SandboxUnavailable("paused"), 503, "MACHINE_NOT_READY"),
        (SandboxNotReady("booting"), 503, "MACHINE_NOT_READY"),
        (
            SandboxUnauthorized("HTTP 401"),
            503,
            "MACHINE_RUNTIME_UNAUTHORIZED",
        ),
        (
            WorkspaceRuntimeUnauthorized("HTTP 401", status_code=401),
            503,
            "MACHINE_RUNTIME_UNAUTHORIZED",
        ),
        (WorkspaceRuntimeError("odd"), 503, "MACHINE_UNREACHABLE"),
        (SandboxError("odd"), 503, "MACHINE_UNREACHABLE"),
    ],
)
def test_each_failure_has_one_answer(exc, status, code) -> None:
    refusal = machine_call_failed(exc, path="/home/user/lemma/a")

    assert (refusal.status_code, refusal.code) == (status, code)


def test_a_refused_credential_is_not_reported_as_a_file_that_is_too_large() -> None:
    """Every refusal's class name carries "Rejected", and the old mapping
    keyed on the word: a runtime refusing Lemma's own credential reached the
    file explorer as 413 "File is larger than this endpoint will serve"."""
    refusal = machine_call_failed(SandboxUnauthorized("HTTP 401"))

    assert refusal.status_code == 503
    assert "larger" not in refusal.message


def test_a_sandbox_that_is_gone_is_not_a_missing_path() -> None:
    """`SandboxNotFound` is the machine, not the file: "NotFound" in its name
    sent it to "No such path" too."""
    assert machine_call_failed(SandboxNotFound("gone")).code != "MACHINE_PATH_NOT_FOUND"
    assert not is_missing_path(SandboxNotFound("gone"))
    assert is_missing_path(SandboxPathNotFound("gone"))


def test_the_path_is_named_when_there_is_one() -> None:
    refusal = machine_call_failed(SandboxPathNotFound("x"), path="/home/user/a")

    assert "/home/user/a" in refusal.message


def test_a_defect_is_not_in_the_table() -> None:
    assert not isinstance(TypeError("bug"), MACHINE_FAILURES)


def _app(raised: BaseException) -> TestClient:
    from fastapi import APIRouter

    router = APIRouter(route_class=MachineFailureRoute)

    @router.get("/probe")
    async def probe() -> None:
        raise raised

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_a_route_answers_an_escaped_failure_from_the_table() -> None:
    response = _app(SandboxProcessNotFound("gone")).get("/probe")

    assert response.status_code == 404
    assert "MACHINE_PROCESS_NOT_FOUND" in response.text


def test_a_route_leaves_a_defect_a_500() -> None:
    assert _app(TypeError("bug")).get("/probe").status_code == 500
