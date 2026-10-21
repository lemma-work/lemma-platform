"""A refused request is described, not echoed back.

Pydantic attaches to each validation error the value it refused as ``input``
-- the whole body for a missing field. A request is refused for being wrong,
not for being harmless: a malformed header map carries its credential, an
over-long message all of itself. Each error keeps where (``loc``), which rule
(``type``) and the rule's words (``msg``), and the rule's own parameters in
``ctx``; never the input.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

from app.core.api.exception_handlers import register_exception_handlers

pytestmark = pytest.mark.unit

_SECRET = "sk-live-not-a-real-credential-0123456789"


class _Message(BaseModel):
    content: str = Field(max_length=100)
    title: str


class _Profile(BaseModel):
    base_url: HttpUrl
    headers: dict[str, str]


class _Token(BaseModel):
    """Validators worded both ways: one quoting its input, one naming its rule."""

    token: str
    label: str = "default"

    @field_validator("token")
    @classmethod
    def _quotes_its_input(cls, value: str) -> str:
        if not value.startswith("tok_"):
            raise ValueError(f"Invalid value: {value}")
        return value

    @field_validator("label")
    @classmethod
    def _names_its_rule(cls, value: str) -> str:
        assert value.strip(), f"label must not be blank, got {value!r}"
        if len(value) > 20:
            raise ValueError("label must be at most 20 characters")
        return value


class _Schedule(BaseModel):
    """A whole-body rule that names the enum value the request chose."""

    schedule_type: str
    operations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _datastore_needs_operations(self) -> "_Schedule":
        if self.schedule_type == "DATASTORE" and not self.operations:
            raise ValueError("DATASTORE schedules must declare operations explicitly")
        return self


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/messages")
    async def _send(body: _Message) -> dict[str, str]:
        return {"ok": body.content}

    @app.post("/profiles")
    async def _create(body: _Profile) -> dict[str, str]:
        return {"ok": str(body.base_url)}

    @app.post("/tokens")
    async def _store(body: _Token) -> dict[str, str]:
        return {"ok": body.label}

    @app.post("/schedules")
    async def _schedule(body: _Schedule) -> dict[str, str]:
        return {"ok": body.schedule_type}

    return TestClient(app, raise_server_exceptions=False)


def _errors(response: Response) -> list[dict[str, object]]:
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    return body["details"]


def test_a_secret_in_a_refused_field_is_not_sent_back(client: TestClient) -> None:
    response = client.post(
        "/profiles",
        json={
            "base_url": f"not a url {_SECRET}",
            "headers": f"Authorization: Bearer {_SECRET}",
        },
    )

    errors = _errors(response)
    assert _SECRET not in response.text
    assert {tuple(error["loc"]) for error in errors} == {  # type: ignore[arg-type]
        ("body", "base_url"),
        ("body", "headers"),
    }
    assert all(set(error) <= {"loc", "type", "msg", "ctx"} for error in errors)


def test_a_long_body_is_not_sent_back(client: TestClient) -> None:
    notes = "y" * 60_000

    response = client.post(
        "/messages", json={"content": "ok", "notes": notes, "secret": _SECRET}
    )

    (error,) = _errors(response)
    assert error["loc"] == ["body", "title"]
    assert error["type"] == "missing"
    assert "input" not in error
    assert _SECRET not in response.text
    assert len(response.text) < 1_000


def test_the_rule_is_kept_and_the_value_is_not(client: TestClient) -> None:
    response = client.post("/messages", json={"content": "x" * 60_000, "title": "t"})

    (error,) = _errors(response)
    assert error["loc"] == ["body", "content"]
    assert error["type"] == "string_too_long"
    assert error["ctx"] == {"max_length": 100}
    assert "input" not in error
    assert "xxxx" not in response.text


def test_a_validator_message_that_quotes_the_input_is_replaced(
    client: TestClient,
) -> None:
    response = client.post("/tokens", json={"token": _SECRET})

    (error,) = _errors(response)
    assert error["loc"] == ["body", "token"]
    assert error["type"] == "value_error"
    assert error["msg"] == "Value error, the value is not valid"
    assert "ctx" not in error
    assert _SECRET not in response.text


def test_an_assertion_that_quotes_the_input_is_replaced(client: TestClient) -> None:
    blank = " " * 8

    response = client.post("/tokens", json={"token": "tok_1", "label": blank})

    (error,) = _errors(response)
    assert error["type"] == "assertion_error"
    assert error["msg"] == "Assertion failed, the value is not valid"
    assert blank not in response.text


def test_a_validator_message_that_names_its_rule_is_kept(client: TestClient) -> None:
    response = client.post("/tokens", json={"token": "tok_1", "label": "z" * 30})

    (error,) = _errors(response)
    assert error["type"] == "value_error"
    assert error["msg"] == "Value error, label must be at most 20 characters"


def test_a_rule_that_names_the_enum_value_chosen_is_kept(client: TestClient) -> None:
    """An enum constant is the schema's word, not the request's secret."""
    response = client.post("/schedules", json={"schedule_type": "DATASTORE"})

    (error,) = _errors(response)
    assert error["type"] == "value_error"
    assert "declare operations explicitly" in str(error["msg"])


def test_only_the_rules_own_context_is_kept() -> None:
    from app.core.api.exception_handlers import _described

    described = _described(
        {
            "type": "union_tag_invalid",
            "loc": ("body", "kind"),
            "msg": "Input tag 'x' found using 'kind' does not match any of the expected tags",
            "input": {"kind": "x"},
            "ctx": {
                "discriminator": "'kind'",
                "tag": f"{_SECRET}-part",
                "expected_tags": "'a', 'b'",
                "error": f"near {_SECRET}",
            },
        }
    )

    assert described["ctx"] == {"discriminator": "'kind'", "expected_tags": "'a', 'b'"}
