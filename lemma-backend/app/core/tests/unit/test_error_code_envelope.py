"""The `code` a client branches on, when the raiser used `HTTPException`.

`DomainError` carries a real code and the handler passes it through. An
`HTTPException` does not, so its handler synthesised `HTTP_403` -- and a caller
matching on `ACCOUNT_INACTIVE` matched nothing. Several places already saw that
coming and passed the code in the detail, as
`detail={"code": ..., "message": ...}`; nothing read it, so the whole dict was
str()'d into the `message` field and shipped as a stringified Python literal
with the real code inside it.

That convention is what these pin. `HTTP_<status>` stays the answer for a plain
string detail, which is the honest one: nobody named a code there.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.api.exception_handlers import register_exception_handlers

pytestmark = pytest.mark.unit


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/named")
    async def _named():
        raise HTTPException(
            status_code=403,
            detail={"code": "ACCOUNT_INACTIVE", "message": "This account is inactive."},
        )

    @app.get("/unnamed")
    async def _unnamed():
        raise HTTPException(status_code=404, detail="Nothing here")

    @app.get("/other-mapping")
    async def _other_mapping():
        raise HTTPException(status_code=400, detail={"field": "name"})

    return TestClient(app, raise_server_exceptions=False)


def test_a_named_code_in_the_detail_reaches_the_client(client):
    response = client.get("/named")

    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "ACCOUNT_INACTIVE"
    assert body["message"] == "This account is inactive."


def test_a_plain_detail_still_reports_the_status(client):
    response = client.get("/unnamed")

    assert response.json()["code"] == "HTTP_404"
    assert response.json()["message"] == "Nothing here"


def test_a_mapping_without_a_code_is_left_alone(client):
    """Only the `{code, message}` convention is special; nothing else changes."""
    body = client.get("/other-mapping").json()

    assert body["code"] == "HTTP_400"
