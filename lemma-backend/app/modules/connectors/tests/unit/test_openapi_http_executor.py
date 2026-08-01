"""Unit tests for the package-free OpenAPI HTTP executor (httpx MockTransport)."""

from __future__ import annotations

import base64

import httpx
import pytest

from app.modules.connectors.infrastructure.adapters.openapi_http_executor import (
    OpenApiHttpExecutionError,
    OpenApiHttpExecutor,
)

CREDS = {"access_token": "tok123", "token_type": "Bearer"}


def _executor(handler) -> OpenApiHttpExecutor:
    """Executor driving an injected MockTransport client.

    The client is injected rather than built per call, so tests hand one in the
    same way the composition layer hands in the process-shared pool.
    """
    return OpenApiHttpExecutor(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


async def _run(monkeypatch, execution, payload, handler):
    return await _executor(handler).execute(
        connector_id="github",
        operation_name="op",
        execution=execution,
        payload=payload,
        third_party_credentials=CREDS,
    )


@pytest.mark.asyncio
async def test_get_path_substitution_and_array_query(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, json={"ok": True})

    hr = {
        "mode": "openapi",
        "method": "GET",
        "path": "/repos/{owner}/{repo}/issues",
        "server_url": "https://api.github.com",
        "path_params": ["owner", "repo"],
        "query_params": [{"name": "labels", "style": "form", "explode": False}],
        "header_params": [],
        "request_body": None,
        "response": {"binary": False},
        "default_headers": {"User-Agent": "lemma"},
    }
    result = await _run(monkeypatch, hr, {"owner": "me", "repo": "demo", "labels": ["bug", "p1"]}, handler)
    assert result == {"ok": True}
    assert seen["url"] == "https://api.github.com/repos/me/demo/issues?labels=bug%2Cp1"
    assert seen["auth"] == "Bearer tok123"
    assert seen["ua"] == "lemma"


@pytest.mark.asyncio
async def test_post_json_body(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["content_type"] = request.headers.get("content-type")
        seen["body"] = request.content
        return httpx.Response(201, json={"number": 7})

    hr = {
        "mode": "openapi",
        "method": "POST",
        "path": "/repos/{owner}/{repo}/issues",
        "server_url": "https://api.github.com",
        "path_params": ["owner", "repo"],
        "query_params": [],
        "header_params": [],
        "request_body": {"content_type": "application/json", "field": "body", "binary_fields": [], "form_fields": []},
        "response": {"binary": False},
    }
    result = await _run(
        monkeypatch, hr, {"owner": "me", "repo": "demo", "body": {"title": "hi"}}, handler
    )
    assert result == {"number": 7}
    assert seen["method"] == "POST"
    assert "application/json" in seen["content_type"]
    assert b'"title"' in seen["body"]


@pytest.mark.asyncio
async def test_multipart_upload_from_base64(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content_type"] = request.headers.get("content-type")
        seen["content"] = request.content
        return httpx.Response(201, json={"uploaded": True})

    hr = {
        "mode": "openapi",
        "method": "POST",
        "path": "/upload",
        "server_url": "https://api.example.com",
        "path_params": [],
        "query_params": [],
        "header_params": [],
        "request_body": {
            "content_type": "multipart/form-data",
            "field": "body",
            "binary_fields": ["file"],
            "form_fields": ["name"],
        },
        "response": {"binary": False},
    }
    payload = {"body": {"file": {"base64": base64.b64encode(b"hello-bytes").decode()}, "name": "a.txt"}}
    result = await _run(monkeypatch, hr, payload, handler)
    assert result == {"uploaded": True}
    assert seen["content_type"].startswith("multipart/form-data")
    assert b"hello-bytes" in seen["content"]
    assert b"a.txt" in seen["content"]


@pytest.mark.asyncio
async def test_octet_stream_single_body(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content_type"] = request.headers.get("content-type")
        seen["content"] = request.content
        return httpx.Response(201, json={"ok": True})

    hr = {
        "mode": "openapi",
        "method": "POST",
        "path": "/assets",
        "server_url": "https://uploads.example.com",
        "path_params": [],
        "query_params": [],
        "header_params": [],
        "request_body": {"content_type": "application/octet-stream", "field": "body", "binary_fields": ["body"], "form_fields": []},
        "response": {"binary": False},
    }
    payload = {"body": {"base64": base64.b64encode(b"\x00\x01\x02rawblob").decode()}}
    await _run(monkeypatch, hr, payload, handler)
    assert seen["content_type"] == "application/octet-stream"
    assert seen["content"] == b"\x00\x01\x02rawblob"


@pytest.mark.asyncio
async def test_binary_response_returns_binary_content_result(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"tarball-bytes",
            headers={"content-type": "application/gzip", "content-disposition": 'attachment; filename="out.tgz"'},
        )

    hr = {
        "mode": "openapi",
        "method": "GET",
        "path": "/tarball",
        "server_url": "https://api.github.com",
        "path_params": [],
        "query_params": [],
        "header_params": [],
        "request_body": None,
        "response": {"binary": True},
    }
    result = await _run(monkeypatch, hr, {}, handler)
    assert result.type == "binary_content"
    assert base64.b64decode(result.content_base64) == b"tarball-bytes"
    assert result.file_name == "out.tgz"


@pytest.mark.asyncio
async def test_missing_path_param_raises(monkeypatch):
    hr = {
        "mode": "openapi",
        "method": "GET",
        "path": "/repos/{owner}",
        "server_url": "https://api.github.com",
        "path_params": ["owner"],
        "query_params": [],
        "header_params": [],
        "request_body": None,
        "response": {"binary": False},
    }
    with pytest.raises(OpenApiHttpExecutionError, match="path parameter"):
        await _run(monkeypatch, hr, {}, lambda r: httpx.Response(200))


@pytest.mark.asyncio
async def test_non_2xx_raises_with_status(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "Validation failed"})

    hr = {
        "mode": "openapi",
        "method": "POST",
        "path": "/x",
        "server_url": "https://api.github.com",
        "path_params": [],
        "query_params": [],
        "header_params": [],
        "request_body": None,
        "response": {"binary": False},
    }
    with pytest.raises(OpenApiHttpExecutionError) as exc:
        await _run(monkeypatch, hr, {}, handler)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_raw_mode_and_ssrf_guard(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"path": str(request.url.path)})

    hr = {"mode": "raw", "server_url": "https://api.github.com"}
    result = await _run(monkeypatch, hr, {"method": "GET", "path": "/repos/o/r"}, handler)
    assert result == {"path": "/repos/o/r"}

    with pytest.raises(OpenApiHttpExecutionError, match="absolute path"):
        await _run(monkeypatch, hr, {"method": "GET", "path": "https://evil.com/x"}, handler)

    with pytest.raises(OpenApiHttpExecutionError, match="absolute path"):
        await _run(monkeypatch, hr, {"method": "GET", "path": "//evil.com/x"}, handler)
