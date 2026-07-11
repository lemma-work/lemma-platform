"""Guardrails for the unified error-handling contract.

1. Every domain error across the app subclasses ``DomainError`` so the global
   handler maps it to a real HTTP status (not a 500). This invariant is exactly
   what was violated when the ``agent`` module defined its errors on plain
   ``Exception`` and ``GET .../messages`` started returning 500 instead of 404.

2. Every handler emits the one unified envelope ``{message, code, details}``.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

import app.modules as modules_pkg
from app.core.api.exception_handlers import register_exception_handlers
from app.core.domain.errors import BadRequestError, DomainError

pytestmark = pytest.mark.unit


def _iter_domain_error_modules():
    """Import every ``app.modules.<name>.domain.errors`` that exists."""
    for mod in pkgutil.iter_modules(modules_pkg.__path__):
        name = f"app.modules.{mod.name}.domain.errors"
        try:
            yield importlib.import_module(name)
        except ModuleNotFoundError:
            continue  # not every module declares domain errors


def test_all_domain_errors_subclass_domain_error() -> None:
    """A new ``class FooError(Exception)`` in any domain/errors.py fails here."""
    seen = 0
    for errors_mod in _iter_domain_error_modules():
        for value in vars(errors_mod).values():
            if (
                isinstance(value, type)
                and issubclass(value, BaseException)
                and value.__module__ == errors_mod.__name__
            ):
                seen += 1
                assert issubclass(value, DomainError), (
                    f"{errors_mod.__name__}.{value.__name__} must subclass "
                    "DomainError or the global handler returns 500 instead of a "
                    "real status code"
                )
    assert seen, "no domain error classes discovered — test is mis-wired"


def _envelope_app() -> FastAPI:
    test_app = FastAPI()
    register_exception_handlers(test_app)

    class _Body(BaseModel):
        n: int

    @test_app.get("/domain")
    async def _domain():
        raise DomainError("nope", code="WIDGET_GONE", status_code=404, details={"x": 1})

    @test_app.get("/http")
    async def _http():
        raise HTTPException(status_code=403, detail="forbidden")

    @test_app.post("/validate")
    async def _validate(body: _Body):
        return body

    @test_app.get("/boom")
    async def _boom():
        raise RuntimeError("kaboom")

    return test_app


def test_handlers_emit_unified_envelope() -> None:
    client = TestClient(
        _envelope_app(),
        raise_server_exceptions=False,
        headers={"x-request-id": "test-request-id"},
    )

    r = client.get("/domain")
    assert r.status_code == 404
    assert r.json() == {
        "message": "nope",
        "code": "WIDGET_GONE",
        "request_id": "test-request-id",
        "details": {"x": 1},
    }

    r = client.get("/http")
    assert r.status_code == 403
    assert r.json() == {
        "message": "forbidden",
        "code": "HTTP_403",
        "request_id": "test-request-id",
        "details": None,
    }

    r = client.post("/validate", json={"n": "not-an-int"})
    body = r.json()
    assert r.status_code == 422
    assert body["code"] == "VALIDATION_ERROR"
    assert body["request_id"] == "test-request-id"
    assert body["message"] == "Request validation failed"
    assert isinstance(body["details"], list)

    r = client.get("/boom")
    assert r.status_code == 500
    assert r.json() == {
        "message": "Internal server error",
        "code": "INTERNAL_ERROR",
        "request_id": "test-request-id",
        "details": None,
    }


def test_agent_and_bad_request_errors_map_to_status() -> None:
    """Regression for the original bug: agent errors must carry a real status."""
    from app.modules.agent.domain import errors as agent_errors

    assert agent_errors.ConversationNotFoundError().status_code == 404
    assert agent_errors.AgentNotFoundError().status_code == 404
    assert agent_errors.AgentAlreadyExistsError().status_code == 409
    assert agent_errors.AgentValidationError().status_code == 400

    bad = BadRequestError("Invalid page_token")
    assert bad.status_code == 400 and bad.code == "BAD_REQUEST"
