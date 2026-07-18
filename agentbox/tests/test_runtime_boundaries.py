from __future__ import annotations

import asyncio
import http.client
import concurrent.futures
import io
import json
import logging
import os
import sys
import time
import types
from types import SimpleNamespace
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from uuid import uuid4
from urllib import error, request

import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import ValidationError
from starlette.requests import Request as StarletteRequest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if "kubernetes" not in sys.modules:
    kubernetes_module = types.ModuleType("kubernetes")
    kubernetes_client_module = types.ModuleType("kubernetes.client")
    kubernetes_config_module = types.ModuleType("kubernetes.config")
    kubernetes_stream_module = types.ModuleType("kubernetes.stream")
    kubernetes_client_rest_module = types.ModuleType("kubernetes.client.rest")

    class _ApiException(Exception):
        def __init__(self, status=None, reason=None, body=None):
            super().__init__(reason)
            self.status = status
            self.reason = reason
            self.body = body

    class _ConfigException(Exception):
        pass

    kubernetes_client_rest_module.ApiException = _ApiException
    kubernetes_config_module.ConfigException = _ConfigException
    kubernetes_module.client = kubernetes_client_module
    kubernetes_module.config = kubernetes_config_module
    kubernetes_module.stream = kubernetes_stream_module

    sys.modules["kubernetes"] = kubernetes_module
    sys.modules["kubernetes.client"] = kubernetes_client_module
    sys.modules["kubernetes.config"] = kubernetes_config_module
    sys.modules["kubernetes.stream"] = kubernetes_stream_module
    sys.modules["kubernetes.client.rest"] = kubernetes_client_rest_module

from agentbox import (  # noqa: E402
    endpoint_transport,
    kubernetes,
    runtime_kernel,
    runtime_proxy,
    runtime_server,
)
from agentbox.api import apps  # noqa: E402
from agentbox.providers.legacy import LegacyRuntimeProviderMixin  # noqa: E402
from agentbox.providers.models import SandboxEndpoint  # noqa: E402
from agentbox.runtime_proxy import RuntimeProxy  # noqa: E402
from agentbox.schemas import (  # noqa: E402
    ExecCommandRequest,
    SandboxInternalAppStatus,
    SandboxInternalStatus,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_linux_kernel_hardening_does_not_bind_lifetime_to_request_thread(
    monkeypatch,
):
    calls: list[tuple[int, int, int, int, int]] = []

    class _LibC:
        def prctl(self, *args):
            calls.append(args)
            return 0

    monkeypatch.setattr(runtime_kernel.sys, "platform", "linux")
    monkeypatch.setattr(runtime_kernel.ctypes, "CDLL", lambda *args, **kwargs: _LibC())

    runtime_kernel._harden_child_process()

    # PR_SET_DUMPABLE protects credentials. PR_SET_PDEATHSIG must not be used:
    # the kernel is spawned by a short-lived ThreadingHTTPServer request thread.
    assert calls == [(4, 0, 0, 0, 0)]


@pytest.fixture(autouse=True)
def cleanup_runtime_sessions():
    yield
    for session_id in list(runtime_server.sessions):
        runtime_server.delete_session(session_id)


class _FakeProxyProvider:
    async def get_status(self, sandbox_id: str) -> SandboxInternalStatus:
        return SandboxInternalStatus(
            id=sandbox_id,
            status="RUNNING",
            ready=True,
            pod_ip="127.0.0.1",
            apps={
                "function_executor": SandboxInternalAppStatus(
                    name="function_executor",
                    public_slug="function",
                    port=8090,
                    ready=True,
                    private_url="http://function-executor",
                )
            },
        )


class _FakeE2BProxyProvider(_FakeProxyProvider):
    async def get_status(self, sandbox_id: str) -> SandboxInternalStatus:
        del sandbox_id
        raise AssertionError("authenticated endpoint resolution must not poll status")

    async def resolve_endpoint(self, sandbox_id, app_spec, *, protocol="http"):
        del sandbox_id, app_spec, protocol
        return SandboxEndpoint(
            base_url="http://function-executor",
            instance_id="e2b-provider-1",
            transient_gateway="e2b",
        )


class _RefreshingE2BProxyProvider(_FakeProxyProvider):
    def __init__(self) -> None:
        self.resolve_calls = 0
        self.refresh_calls = 0
        self.invalidated: list[str] = []

    async def get_status(self, sandbox_id: str) -> SandboxInternalStatus:
        del sandbox_id
        raise AssertionError("authenticated endpoint resolution must not poll status")

    async def resolve_endpoint(self, sandbox_id, app_spec, *, protocol="http"):
        del sandbox_id, app_spec, protocol
        self.resolve_calls += 1
        generation = self.resolve_calls
        return SandboxEndpoint(
            base_url=f"http://function-executor-{generation}",
            instance_id=f"e2b-provider-{generation}",
            transient_gateway="e2b",
        )

    def invalidate_sandbox_cache(self, sandbox_id: str) -> None:
        self.invalidated.append(sandbox_id)

    async def refresh_endpoint(
        self, sandbox_id, app_spec, *, instance_id, protocol="http"
    ):
        del sandbox_id, app_spec, protocol
        assert instance_id == "e2b-provider-1"
        self.refresh_calls += 1
        return SandboxEndpoint(
            base_url="http://function-executor-2",
            instance_id="e2b-provider-1",
            transient_gateway="e2b",
        )


class _DatabaseRouteManager:
    def __init__(self, endpoint: SandboxEndpoint) -> None:
        self.endpoint = endpoint
        self.endpoint_reads = 0
        self.failures: list[str] = []

    async def database_endpoint(
        self,
        sandbox_id: str,
        app_name: str,
        *,
        expected_generation: int | None = None,
    ):
        del expected_generation
        assert sandbox_id == "sandbox-1"
        assert app_name == "function_executor"
        self.endpoint_reads += 1
        return self.endpoint

    async def signal_route_failure(self, sandbox_id: str, *, reason: str, **kwargs):
        del kwargs
        assert sandbox_id == "sandbox-1"
        self.failures.append(reason)


class _RefreshingE2BRuntimeProvider(LegacyRuntimeProviderMixin):
    def __init__(self) -> None:
        self.resolve_calls = 0
        self.refresh_calls = 0
        self.invalidated: list[str] = []

    async def resolve_endpoint(self, sandbox_id, app_spec, *, protocol="http"):
        del sandbox_id, app_spec, protocol
        self.resolve_calls += 1
        generation = self.resolve_calls
        return SandboxEndpoint(
            base_url=f"http://runtime-{generation}",
            instance_id=f"e2b-provider-{generation}",
            transient_gateway="e2b",
        )

    def invalidate_sandbox_cache(self, sandbox_id: str) -> None:
        self.invalidated.append(sandbox_id)

    async def refresh_endpoint(
        self, sandbox_id, app_spec, *, instance_id, protocol="http"
    ):
        del sandbox_id, app_spec, protocol
        assert instance_id == "e2b-provider-1"
        self.refresh_calls += 1
        return SandboxEndpoint(
            base_url="http://runtime-2",
            instance_id="e2b-provider-1",
            transient_gateway="e2b",
        )


class _FakeUrlResponse:
    def __init__(
        self,
        status: int = 200,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ):
        self.status = status
        self._body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._body


def _starlette_request(
    *,
    method: str = "POST",
    body: bytes = b'{"input": {"value": 1}}',
    query_string: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> StarletteRequest:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return StarletteRequest(
        {
            "type": "http",
            "method": method,
            "path": "/pods/pod/functions/fn/execute",
            "app": SimpleNamespace(state=SimpleNamespace()),
            "headers": [
                (b"content-type", b"application/json"),
                (b"authorization", b"Bearer lemma-token"),
                *(headers or []),
            ],
            "query_string": query_string,
        },
        receive,
    )


def test_runtime_http_error_is_logged_and_returned_bounded(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=kubernetes.__name__)
    oversized_body = {"detail": "x" * 4000}

    def fake_urlopen(req, timeout):
        del req, timeout
        raise error.HTTPError(
            url="http://runtime/sessions/s1/exec-command",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=io.BytesIO(json.dumps(oversized_body).encode("utf-8")),
        )

    monkeypatch.setattr(request, "urlopen", fake_urlopen)
    req = request.Request("http://runtime/sessions/s1/exec-command", method="POST")

    with pytest.raises(HTTPException) as exc_info:
        kubernetes._request_runtime_json(
            req,
            timeout=1,
            operation="process command request",
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["runtime_status"] == 500
    assert exc_info.value.detail["runtime_body"]["truncated"] is True
    assert len(exc_info.value.detail["runtime_body"]["preview"]) <= (
        kubernetes._MAX_RUNTIME_ERROR_BODY_LENGTH + len("... [truncated]")
    )
    assert "agentbox.kubernetes.runtime_returned_http_s.diagnostic" in caplog.text
    assert "x" * 100 not in caplog.text


def test_sandbox_app_url_base_uses_agentbox_api_url(monkeypatch):
    monkeypatch.setattr(apps.settings, "agentbox_api_url", "https://agentbox.test/")
    monkeypatch.setattr(apps.settings, "agentbox_app_domain", "apps.agentbox.test")

    url = apps.sandbox_app_public_host(
        apps.resolve_sandbox_app("browser"),
        "sandbox-1",
    )

    assert url == "sandbox-1-browser.apps.agentbox.test"


def test_sandbox_app_url_uses_resolvable_loopback_domain_by_default(monkeypatch):
    monkeypatch.setattr(apps.settings, "agentbox_api_url", "http://127.0.0.1:8721")
    monkeypatch.setattr(apps.settings, "agentbox_app_domain", None)

    host = apps.sandbox_app_public_host(
        apps.resolve_sandbox_app("browser"),
        "sandbox-1",
    )
    url = apps.sandbox_app_public_url(
        apps.resolve_sandbox_app("browser"),
        "sandbox-1",
        "token-value",
    )

    assert host == "sandbox-1-browser.127-0-0-1.sslip.io:8721"
    assert url == "http://sandbox-1-browser.127-0-0-1.sslip.io:8721/?token=token-value"
    assert apps.sandbox_app_from_host(host)[1] == "sandbox-1"


def test_app_access_token_is_bound_to_app_and_sandbox(monkeypatch):
    monkeypatch.setattr(apps.settings, "agentbox_api_key", "secret")
    expires_at = int(time.time()) + 60
    token = apps.create_app_access_token("sandbox-1", "browser", expires_at)

    assert apps.validate_app_access_token("sandbox-1", "browser", token)
    assert not apps.validate_app_access_token("sandbox-2", "browser", token)
    assert not apps.validate_app_access_token("sandbox-1", "function_executor", token)


def test_app_access_cookie_uses_token_ttl(monkeypatch):
    monkeypatch.setattr(apps.settings, "agentbox_api_url", "https://agentbox.test/")
    monkeypatch.setattr(apps.settings, "agentbox_api_key", "secret")
    expires_at = int(time.time()) + 123
    token = apps.create_app_access_token("sandbox-1", "browser", expires_at)
    response = Response()

    apps.set_app_access_cookie(
        response,
        apps.resolve_sandbox_app("browser"),
        "sandbox-1",
        token,
    )

    cookie = response.headers["set-cookie"]
    assert "agentbox_app_access_browser_sandbox-1=" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "Max-Age=123" in cookie or "Max-Age=122" in cookie


def test_sandbox_app_upstream_websocket_url_strips_access_token():
    status_obj = SandboxInternalStatus(
        id="sandbox-1",
        status="RUNNING",
        ready=True,
        apps={
            "browser": SandboxInternalAppStatus(
                name="browser",
                public_slug="browser",
                port=4848,
                ready=True,
                private_url="http://browser-upstream",
            )
        },
    )

    url = apps.sandbox_app_upstream_websocket_url(
        status_obj,
        apps.resolve_sandbox_app("browser"),
        "api/session/123/stream",
        "token=private-token&x=1",
    )

    assert url == "ws://browser-upstream/api/session/123/stream?x=1"


@pytest.mark.anyio
async def test_sandbox_app_proxy_waits_for_app_health_before_forwarding(monkeypatch):
    request_obj = _starlette_request(query_string=b"x=1&token=discard")
    request_obj.app.state.sandbox_app_ready_cache = set()
    monkeypatch.setattr(apps.settings, "agentbox_sandbox_app_ready_timeout_seconds", 1)

    async def fast_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(apps.asyncio, "sleep", fast_sleep)
    calls: list[str] = []
    health_attempts = 0

    def fake_urlopen(req, timeout):
        del timeout
        nonlocal health_attempts
        if req.full_url == "http://function-executor/health":
            calls.append("health")
            health_attempts += 1
            if health_attempts == 1:
                raise error.URLError("executor not ready")
            return _FakeUrlResponse(body=b'{"status":"ok"}')

        calls.append("forward")
        assert req.full_url == (
            "http://function-executor/pods/pod/functions/fn/execute?x=1"
        )
        assert req.get_method() == "POST"
        assert req.data == b'{"input": {"value": 1}}'
        return _FakeUrlResponse(
            body=b'{"output": {"ok": true}}',
            headers={"Content-Type": "application/json"},
        )

    monkeypatch.setattr(apps.urlrequest, "urlopen", fake_urlopen)

    response = await apps.proxy_sandbox_app_http_request(
        apps.resolve_sandbox_app("function_executor"),
        "sandbox-1",
        "pods/pod/functions/fn/execute",
        request_obj,
        _FakeProxyProvider(),
        forward_authorization=True,
    )

    assert response.status_code == 200
    assert response.body == b'{"output": {"ok": true}}'
    assert calls == ["health", "health", "forward"]


@pytest.mark.anyio
async def test_database_route_proxy_skips_readiness_and_provider_resolution(
    monkeypatch,
):
    request_obj = _starlette_request(body=b'{"input":{"value":1}}')
    manager = _DatabaseRouteManager(
        SandboxEndpoint(base_url="http://function-executor")
    )
    calls: list[str] = []

    def fake_urlopen(req, timeout):
        del timeout
        calls.append(req.full_url)
        return _FakeUrlResponse(
            body=b'{"output":{"ok":true}}',
            headers={"Content-Type": "application/json"},
        )

    monkeypatch.setattr(request, "urlopen", fake_urlopen)
    response = await apps.proxy_sandbox_app_http_request(
        apps.resolve_sandbox_app("function_executor"),
        "sandbox-1",
        "pods/pod/functions/fn/execute",
        request_obj,
        manager,  # type: ignore[arg-type]
        forward_authorization=True,
    )

    assert response.status_code == 200
    assert manager.endpoint_reads == 1
    assert manager.failures == []
    assert calls == ["http://function-executor/pods/pod/functions/fn/execute"]


@pytest.mark.anyio
async def test_database_route_retries_only_intrinsically_idempotent_requests(
    monkeypatch,
):
    manager = _DatabaseRouteManager(
        SandboxEndpoint(
            base_url="https://8090-e2b-provider-1.e2b.app",
            instance_id="e2b-provider-1",
            transient_gateway="e2b",
        )
    )
    calls: list[tuple[str, object]] = []

    def fake_transport(req, **kwargs):
        calls.append((req.get_method(), kwargs.get("retry_seconds", "default")))
        return 200, {"Content-Type": "application/json"}, b'{"status":"ok"}'

    monkeypatch.setattr(apps, "request_endpoint_http", fake_transport)
    for method in ("GET", "POST"):
        response = await apps.proxy_sandbox_app_http_request(
            apps.resolve_sandbox_app("function_executor"),
            "sandbox-1",
            "runs/run-1",
            _starlette_request(method=method, body=b""),
            manager,  # type: ignore[arg-type]
        )
        assert response.status_code == 200

    assert calls == [("GET", "default"), ("POST", 0)]


@pytest.mark.anyio
async def test_application_cannot_forge_gateway_miss_to_trigger_reconcile(
    monkeypatch,
):
    request_obj = _starlette_request(body=b'{"input":{"value":1}}')
    manager = _DatabaseRouteManager(
        SandboxEndpoint(
            base_url="https://8090-e2b-provider-1.e2b.app",
            instance_id="e2b-provider-1",
            transient_gateway="e2b",
        )
    )
    calls = 0

    def fake_urlopen(req, timeout):
        nonlocal calls
        del timeout
        calls += 1
        raise error.HTTPError(
            req.full_url,
            502,
            "Bad Gateway",
            {},
            io.BytesIO(
                b'{"sandboxId":"e2b-provider-1",'
                b'"message":"The sandbox was not found","code":502}'
            ),
        )

    monkeypatch.setattr(request, "urlopen", fake_urlopen)
    response = await apps.proxy_sandbox_app_http_request(
        apps.resolve_sandbox_app("function_executor"),
        "sandbox-1",
        "pods/pod/functions/fn/execute",
        request_obj,
        manager,  # type: ignore[arg-type]
        forward_authorization=True,
    )

    assert response.status_code == 502
    assert calls == 1
    assert manager.failures == []


@pytest.mark.anyio
async def test_sandbox_app_cannot_forge_request_not_delivered_header(monkeypatch):
    request_obj = _starlette_request()
    request_obj.app.state.sandbox_app_ready_cache = set()

    def fake_urlopen(req, timeout):
        del timeout
        if req.full_url == "http://function-executor/health":
            return _FakeUrlResponse(body=b'{"status":"ok"}')
        return _FakeUrlResponse(
            status=503,
            body=b'{"detail":"application failure"}',
            headers={
                "Content-Type": "application/json",
                endpoint_transport.REQUEST_NOT_DELIVERED_HEADER: "true",
            },
        )

    monkeypatch.setattr(apps.urlrequest, "urlopen", fake_urlopen)

    response = await apps.proxy_sandbox_app_http_request(
        apps.resolve_sandbox_app("function_executor"),
        "sandbox-1",
        "pods/pod/functions/fn/execute",
        request_obj,
        _FakeProxyProvider(),
        forward_authorization=True,
    )

    assert response.status_code == 503
    assert response.body == b'{"detail":"application failure"}'
    assert endpoint_transport.REQUEST_NOT_DELIVERED_HEADER not in response.headers


@pytest.mark.anyio
async def test_sandbox_app_proxy_rewrites_origin_and_referer_for_upstream(
    monkeypatch,
):
    public_origin = "http://sandbox-1-browser.127-0-0-1.sslip.io:8721"
    request_obj = _starlette_request(
        method="GET",
        body=b"",
        query_string=b"token=discard&x=1",
        headers=[
            (b"origin", public_origin.encode("utf-8")),
            (b"referer", f"{public_origin}/?token=discard".encode("utf-8")),
        ],
    )
    request_obj.app.state.sandbox_app_ready_cache = set()

    class BrowserProvider:
        async def get_status(self, sandbox_id: str) -> SandboxInternalStatus:
            return SandboxInternalStatus(
                id=sandbox_id,
                status="RUNNING",
                ready=True,
                apps={
                    "browser": SandboxInternalAppStatus(
                        name="browser",
                        public_slug="browser",
                        port=4848,
                        ready=True,
                        private_url="http://browser-upstream",
                    )
                },
            )

    def fake_urlopen(req, timeout):
        del timeout
        if req.full_url == "http://browser-upstream/health":
            return _FakeUrlResponse(body=b'{"status":"ok"}')

        assert req.full_url == "http://browser-upstream/api/session/41497/tabs?x=1"
        assert req.headers["Origin"] == "http://browser-upstream"
        assert req.headers["Referer"] == (
            "http://browser-upstream/api/session/41497/tabs?x=1"
        )
        assert "token=discard" not in req.full_url
        return _FakeUrlResponse(body=b'{"tabs":[]}')

    monkeypatch.setattr(apps.urlrequest, "urlopen", fake_urlopen)

    response = await apps.proxy_sandbox_app_http_request(
        apps.resolve_sandbox_app("browser"),
        "sandbox-1",
        "api/session/41497/tabs",
        request_obj,
        BrowserProvider(),
    )

    assert response.status_code == 200
    assert response.body == b'{"tabs":[]}'


@pytest.mark.anyio
async def test_browser_dashboard_proxy_rewrites_local_dashboard_origins(
    monkeypatch,
):
    public_origin = "http://sandbox-1-browser.127-0-0-1.sslip.io:8721"
    monkeypatch.setattr(apps.settings, "agentbox_api_key", "secret")
    access_token = apps.create_app_access_token(
        "sandbox-1",
        "browser",
        int(time.time()) + 60,
    )
    request_obj = _starlette_request(
        method="GET",
        body=b"",
        headers=[
            (b"host", b"sandbox-1-browser.127-0-0-1.sslip.io:8721"),
        ],
    )
    request_obj.app.state.sandbox_app_ready_cache = set()

    class BrowserProvider:
        async def get_status(self, sandbox_id: str) -> SandboxInternalStatus:
            return SandboxInternalStatus(
                id=sandbox_id,
                status="RUNNING",
                ready=True,
                apps={
                    "browser": SandboxInternalAppStatus(
                        name="browser",
                        public_slug="browser",
                        port=4848,
                        ready=True,
                        private_url="http://browser-upstream",
                    )
                },
            )

    dashboard_chunk = (
        b"fetch(`${td()}/api/chat/status`);"
        b'function td(){return "http://localhost:4848"};'
        b'const ws = "ws://localhost:4848/api/stream";'
        b"function e$(e){let t=`/api/session/${e}/stream`;return t}"
    )

    def fake_urlopen(req, timeout):
        del timeout
        if req.full_url == "http://browser-upstream/health":
            return _FakeUrlResponse(body=b'{"status":"ok"}')

        assert (
            req.full_url == "http://browser-upstream/_next/static/chunks/dashboard.js"
        )
        return _FakeUrlResponse(
            body=dashboard_chunk,
            headers={
                "Content-Type": "application/javascript; charset=utf-8",
                "ETag": "stale-etag",
            },
        )

    monkeypatch.setattr(apps.urlrequest, "urlopen", fake_urlopen)

    response = await apps.proxy_sandbox_app_http_request(
        apps.resolve_sandbox_app("browser"),
        "sandbox-1",
        "_next/static/chunks/dashboard.js",
        request_obj,
        BrowserProvider(),
        access_token=access_token,
    )

    assert response.status_code == 200
    assert b"http://localhost:4848" not in response.body
    assert b"ws://localhost:4848" not in response.body
    assert public_origin.encode("utf-8") in response.body
    assert b"ws://sandbox-1-browser.127-0-0-1.sslip.io:8721/api/stream" in response.body
    assert (
        f"/api/session/${{e}}/stream?token={access_token}".encode("utf-8")
        in response.body
    )
    assert "etag" not in {key.lower() for key in response.headers}


@pytest.mark.anyio
async def test_browser_dashboard_proxy_injects_focused_layout_style(monkeypatch):
    request_obj = _starlette_request(
        method="GET",
        body=b"",
        headers=[
            (b"host", b"sandbox-1-browser.127-0-0-1.sslip.io:8721"),
        ],
    )
    request_obj.app.state.sandbox_app_ready_cache = set()

    class BrowserProvider:
        async def get_status(self, sandbox_id: str) -> SandboxInternalStatus:
            return SandboxInternalStatus(
                id=sandbox_id,
                status="RUNNING",
                ready=True,
                apps={
                    "browser": SandboxInternalAppStatus(
                        name="browser",
                        public_slug="browser",
                        port=4848,
                        ready=True,
                        private_url="http://browser-upstream",
                    )
                },
            )

    def fake_urlopen(req, timeout):
        del timeout
        if req.full_url == "http://browser-upstream/health":
            return _FakeUrlResponse(body=b'{"status":"ok"}')

        assert req.full_url == "http://browser-upstream/"
        return _FakeUrlResponse(
            body=b"<!doctype html><html><head><title>agent-browser</title></head><body></body></html>",
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "ETag": "stale-etag",
            },
        )

    monkeypatch.setattr(apps.urlrequest, "urlopen", fake_urlopen)

    response = await apps.proxy_sandbox_app_http_request(
        apps.resolve_sandbox_app("browser"),
        "sandbox-1",
        "",
        request_obj,
        BrowserProvider(),
    )

    assert response.status_code == 200
    assert b"agentbox-browser-dashboard-focus-style" in response.body
    assert b"#activity" in response.body
    assert response.body.index(
        b"agentbox-browser-dashboard-focus-style"
    ) < response.body.index(b"</head>")
    assert "etag" not in {key.lower() for key in response.headers}


@pytest.mark.anyio
async def test_sandbox_app_proxy_maps_closed_upstream_connection_to_502(monkeypatch):
    request_obj = _starlette_request()
    request_obj.app.state.sandbox_app_ready_cache = {
        ("sandbox-1", "function_executor", "http://function-executor")
    }

    def fake_urlopen(req, timeout):
        del req, timeout
        raise http.client.RemoteDisconnected("closed during startup")

    monkeypatch.setattr(apps.urlrequest, "urlopen", fake_urlopen)

    with pytest.raises(HTTPException) as exc_info:
        await apps.proxy_sandbox_app_http_request(
            apps.resolve_sandbox_app("function_executor"),
            "sandbox-1",
            "pods/pod/functions/fn/execute",
            request_obj,
            _FakeProxyProvider(),
            forward_authorization=True,
        )

    assert exc_info.value.status_code == 502
    assert "Sandbox app proxy failed" in exc_info.value.detail


@pytest.mark.anyio
async def test_sandbox_app_proxy_does_not_replay_post_with_forgeable_e2b_body(
    monkeypatch,
):
    request_obj = _starlette_request(
        body=(b'{"run_id":"00000000-0000-4000-8000-000000000001","input":{"value":1}}')
    )
    request_obj.app.state.sandbox_app_ready_cache = {
        (
            "sandbox-1",
            "function_executor",
            "http://function-executor",
            "e2b-provider-1",
        )
    }
    calls = 0

    def fake_urlopen(req, timeout):
        nonlocal calls
        del timeout
        calls += 1
        raise error.HTTPError(
            req.full_url,
            502,
            "Bad Gateway",
            {},
            io.BytesIO(
                b'{"sandboxId":"e2b-provider-1",'
                b'"message":"The sandbox was not found","code":502}'
            ),
        )

    monkeypatch.setattr(request, "urlopen", fake_urlopen)
    monkeypatch.setattr(endpoint_transport.time, "sleep", lambda _: None)

    response = await apps.proxy_sandbox_app_http_request(
        apps.resolve_sandbox_app("function_executor"),
        "sandbox-1",
        "pods/pod/functions/fn/execute",
        request_obj,
        _FakeE2BProxyProvider(),
        forward_authorization=True,
    )

    assert response.status_code == 502
    assert endpoint_transport.REQUEST_NOT_DELIVERED_HEADER not in response.headers
    assert calls == 1


@pytest.mark.anyio
async def test_sandbox_app_proxy_does_not_refresh_after_post_route_miss(
    monkeypatch,
):
    execute_body = (
        b'{"run_id":"00000000-0000-4000-8000-000000000001","input":{"value":1}}'
    )
    request_obj = _starlette_request(body=execute_body)
    request_obj.app.state.sandbox_app_ready_cache = set()
    provider = _RefreshingE2BProxyProvider()
    calls: list[tuple[str, bytes | None]] = []

    def fake_urlopen(req, timeout):
        del timeout
        calls.append((req.full_url, req.data))
        if req.full_url.endswith("/health"):
            return _FakeUrlResponse(body=b'{"status":"ok"}')
        if req.full_url.startswith("http://function-executor-1/"):
            raise error.HTTPError(
                req.full_url,
                502,
                "Bad Gateway",
                {},
                io.BytesIO(
                    b'{"sandboxId":"e2b-provider-1",'
                    b'"message":"The sandbox was not found","code":502}'
                ),
            )
        return _FakeUrlResponse(
            body=b'{"output":{"ok":true}}',
            headers={"Content-Type": "application/json"},
        )

    def request_once(*args, **kwargs):
        return endpoint_transport.request_endpoint_http(
            *args,
            **kwargs,
            retry_seconds=0,
        )

    monkeypatch.setattr(request, "urlopen", fake_urlopen)
    monkeypatch.setattr(apps, "request_endpoint_http", request_once)

    response = await apps.proxy_sandbox_app_http_request(
        apps.resolve_sandbox_app("function_executor"),
        "sandbox-1",
        "pods/pod/functions/fn/execute",
        request_obj,
        provider,
        forward_authorization=True,
    )

    assert response.status_code == 502
    assert provider.resolve_calls == 1
    assert provider.refresh_calls == 0
    assert provider.invalidated == []
    assert calls == [
        ("http://function-executor-1/health", None),
        (
            "http://function-executor-1/pods/pod/functions/fn/execute",
            execute_body,
        ),
    ]


@pytest.mark.anyio
async def test_sandbox_app_proxy_refreshes_when_readiness_route_is_missing(
    monkeypatch,
):
    execute_body = (
        b'{"run_id":"00000000-0000-4000-8000-000000000001","input":{"value":1}}'
    )
    request_obj = _starlette_request(body=execute_body)
    request_obj.app.state.sandbox_app_ready_cache = set()
    provider = _RefreshingE2BProxyProvider()
    calls: list[str] = []

    def fake_urlopen(req, timeout):
        del timeout
        calls.append(req.full_url)
        if req.full_url == "http://function-executor-1/health":
            raise error.HTTPError(
                req.full_url,
                502,
                "Bad Gateway",
                {},
                io.BytesIO(
                    b'{"sandboxId":"e2b-provider-1",'
                    b'"message":"The sandbox was not found","code":502}'
                ),
            )
        if req.full_url == "http://function-executor-2/health":
            return _FakeUrlResponse(body=b'{"status":"ok"}')
        return _FakeUrlResponse(
            body=b'{"output":{"ok":true}}',
            headers={"Content-Type": "application/json"},
        )

    def request_once(*args, **kwargs):
        return endpoint_transport.request_endpoint_http(
            *args,
            **kwargs,
            retry_seconds=0,
        )

    monkeypatch.setattr(request, "urlopen", fake_urlopen)
    monkeypatch.setattr(apps, "request_endpoint_http", request_once)

    response = await apps.proxy_sandbox_app_http_request(
        apps.resolve_sandbox_app("function_executor"),
        "sandbox-1",
        "pods/pod/functions/fn/execute",
        request_obj,
        provider,
        forward_authorization=True,
    )

    assert response.status_code == 200
    assert provider.refresh_calls == 1
    assert calls == [
        "http://function-executor-1/health",
        "http://function-executor-2/health",
        "http://function-executor-2/pods/pod/functions/fn/execute",
    ]


@pytest.mark.anyio
async def test_exhausted_exact_route_refresh_marks_request_not_delivered():
    routing_error = endpoint_transport.EndpointRoutingUnavailable(
        gateway="e2b",
        instance_id="e2b-provider-1",
        port=8090,
    )
    endpoint = SandboxEndpoint(
        base_url="http://function-executor-1",
        instance_id="e2b-provider-1",
        transient_gateway="e2b",
    )

    with pytest.raises(HTTPException) as exc_info:
        await apps.retry_sandbox_app_http_after_route_refresh(
            apps.resolve_sandbox_app("function_executor"),
            "sandbox-1",
            "pods/pod/functions/fn/execute",
            _starlette_request(),
            _RefreshingE2BProxyProvider(),
            routing_error,
            endpoint,
            forward_authorization=True,
            access_token=None,
            endpoint_refresh_attempted=True,
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.headers == {
        "Retry-After": "1",
        endpoint_transport.REQUEST_NOT_DELIVERED_HEADER: "true",
    }
    assert exc_info.value.detail["provider_id"] == "e2b-provider-1"


@pytest.mark.anyio
async def test_sandbox_app_proxy_does_not_replay_forgeable_gateway_body(
    monkeypatch,
):
    request_obj = _starlette_request(body=b'{"input":{"value":1}}')
    request_obj.app.state.sandbox_app_ready_cache = {
        (
            "sandbox-1",
            "function_executor",
            "http://function-executor-1",
            "e2b-provider-1",
        )
    }
    provider = _RefreshingE2BProxyProvider()
    calls = 0

    def fake_urlopen(req, timeout):
        nonlocal calls
        del timeout
        calls += 1
        raise error.HTTPError(
            req.full_url,
            502,
            "Bad Gateway",
            {},
            io.BytesIO(
                b'{"sandboxId":"e2b-provider-1",'
                b'"message":"The sandbox was not found","code":502}'
            ),
        )

    monkeypatch.setattr(request, "urlopen", fake_urlopen)

    response = await apps.proxy_sandbox_app_http_request(
        apps.resolve_sandbox_app("function_executor"),
        "sandbox-1",
        "pods/pod/functions/fn/execute",
        request_obj,
        provider,
        forward_authorization=True,
    )

    assert response.status_code == 502
    assert calls == 1
    assert provider.refresh_calls == 0


@pytest.mark.anyio
async def test_websocket_connect_surfaces_exact_e2b_handshake_miss_for_reconcile():
    endpoint = SandboxEndpoint(
        base_url="https://browser-1.example",
        instance_id="e2b-provider-1",
        transient_gateway="e2b",
    )

    class GatewayMiss(Exception):
        response = SimpleNamespace(
            status_code=502,
            body=(
                b'{"sandboxId":"e2b-provider-1",'
                b'"message":"The sandbox was not found","code":502}'
            ),
        )

    class FakeWebsockets:
        def __init__(self) -> None:
            self.calls = 0

        async def connect(self, *args, **kwargs):
            del args, kwargs
            self.calls += 1
            raise GatewayMiss

    websockets = FakeWebsockets()
    with pytest.raises(endpoint_transport.EndpointRoutingUnavailable):
        await apps.connect_sandbox_app_websocket(
            websockets,
            "sandbox-1",
            apps.resolve_sandbox_app("browser"),
            "api/session/stream",
            "",
            endpoint,
        )

    assert websockets.calls == 1


@pytest.mark.anyio
async def test_websocket_relay_treats_downstream_disconnect_as_normal():
    upstream_closed = asyncio.Event()

    class Downstream:
        async def receive(self):
            return {"type": "websocket.disconnect"}

        async def send_text(self, message):
            del message
            raise AssertionError("relay sent after downstream disconnect")

        async def send_bytes(self, message):
            del message
            raise AssertionError("relay sent after downstream disconnect")

    class Upstream:
        async def close(self):
            upstream_closed.set()

        def __aiter__(self):
            return self

        async def __anext__(self):
            await upstream_closed.wait()
            return "late-message"

    await apps.relay_app_websocket(Downstream(), Upstream())


@pytest.mark.anyio
async def test_websocket_relay_treats_upstream_close_as_normal():
    from websockets.exceptions import ConnectionClosedError

    class Downstream:
        async def receive(self):
            await asyncio.Event().wait()

        async def send_text(self, message):
            del message

        async def send_bytes(self, message):
            del message

    class Upstream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise ConnectionClosedError(None, None)

    await apps.relay_app_websocket(Downstream(), Upstream())


@pytest.mark.anyio
async def test_sandbox_app_proxy_never_refreshes_ambiguous_application_502(
    monkeypatch,
):
    request_obj = _starlette_request()
    request_obj.app.state.sandbox_app_ready_cache = set()
    provider = _RefreshingE2BProxyProvider()
    execute_calls = 0

    def fake_urlopen(req, timeout):
        nonlocal execute_calls
        del timeout
        if req.full_url.endswith("/health"):
            return _FakeUrlResponse(body=b'{"status":"ok"}')
        execute_calls += 1
        raise error.HTTPError(
            req.full_url,
            502,
            "Bad Gateway",
            {},
            io.BytesIO(b'{"detail":"application may have handled request"}'),
        )

    monkeypatch.setattr(request, "urlopen", fake_urlopen)

    response = await apps.proxy_sandbox_app_http_request(
        apps.resolve_sandbox_app("function_executor"),
        "sandbox-1",
        "pods/pod/functions/fn/execute",
        request_obj,
        provider,
        forward_authorization=True,
    )

    assert response.status_code == 502
    assert execute_calls == 1
    assert provider.resolve_calls == 1
    assert provider.invalidated == []


def test_runtime_shell_command_persists_cwd_and_hides_marker(tmp_path):
    session_id = f"test-{uuid4().hex}"
    nested = tmp_path / "nested"
    nested.mkdir()

    first = runtime_server.execute_shell_command(
        session_id,
        f"cd {nested} && printf hello",
        timeout_seconds=5,
        cwd=str(tmp_path),
    )
    second = runtime_server.execute_shell_command(
        session_id,
        "pwd",
        timeout_seconds=5,
    )

    assert first["ok"] is True
    assert first["stdout"] == "hello"
    assert "__AGENTBOX_CWD_" not in first["stdout"]
    assert runtime_server.get_or_create_session(session_id).cwd == str(nested)
    assert second["stdout"].strip() == str(nested)


def test_exec_command_request_rejects_shell_and_login_fields():
    with pytest.raises(ValidationError):
        ExecCommandRequest.model_validate({"cmd": "printf hi", "shell": "/bin/sh"})

    with pytest.raises(ValidationError):
        ExecCommandRequest.model_validate({"cmd": "printf hi", "login": True})


def test_runtime_python_session_behaves_like_notebook(tmp_path):
    session_id = f"test-{uuid4().hex}"
    runtime_server.get_or_create_session(session_id, cwd=str(tmp_path))

    first = runtime_server.execute_python(session_id, "x = 41")
    second = runtime_server.execute_python(session_id, "x + 1")

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["result"] == "42"


def test_runtime_python_captures_native_os_writes_without_corrupting_protocol(
    tmp_path,
):
    session_id = f"test-{uuid4().hex}"
    runtime_server.get_or_create_session(session_id, cwd=str(tmp_path))

    result = runtime_server.execute_python(
        session_id,
        "import os\nos.write(1, b'native stdout\\n')\n"
        "os.write(2, b'native stderr\\n')\n'protocol intact'",
    )

    assert result == {
        "ok": True,
        "stdout": "native stdout\n",
        "stderr": "native stderr\n",
        "result": "'protocol intact'",
        "error_name": None,
    }


def test_runtime_python_captures_subprocess_stdout_and_stderr(tmp_path):
    session_id = f"test-{uuid4().hex}"
    runtime_server.get_or_create_session(session_id, cwd=str(tmp_path))

    result = runtime_server.execute_python(
        session_id,
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-c', "
        "\"import os; os.write(1, b'child stdout\\\\n'); "
        "os.write(2, b'child stderr\\\\n')\"], check=True)\n"
        "73",
    )

    assert result["ok"] is True
    assert result["stdout"] == "child stdout\n"
    assert result["stderr"] == "child stderr\n"
    assert result["result"] == "73"


def test_runtime_python_resolves_typing_annotations_for_schema_extraction(tmp_path):
    # Under Python 3.14 (PEP 649) annotations are evaluated lazily; pydantic
    # resolves a model's deferred annotations via sys.modules[__module__].
    # The session namespace must be a real registered module so imported names
    # like typing.Optional resolve at schema-build time, not just builtins.
    session_id = f"test-{uuid4().hex}"
    runtime_server.get_or_create_session(session_id, cwd=str(tmp_path))

    source = (
        "from typing import Optional\n"
        "from pydantic import BaseModel\n"
        "\n"
        "class Result(BaseModel):\n"
        "    a: Optional[int] = None\n"
        "    b: int | None = None\n"
        "\n"
        "import json\n"
        "json.dumps(Result.model_json_schema())"
    )

    result = runtime_server.execute_python(session_id, source)

    assert result["ok"] is True, result["stderr"]
    schema = json.loads(result["result"].strip("'\""))
    assert set(schema["properties"]) == {"a", "b"}


def test_runtime_python_session_kernel_is_terminated_on_delete(tmp_path):
    session_id = f"test-{uuid4().hex}"
    session = runtime_server.get_or_create_session(session_id, cwd=str(tmp_path))
    result = runtime_server.execute_python(session_id, "value = 42")
    assert result["ok"] is True
    kernel = session.python_kernel
    assert kernel is not None
    assert kernel.process.poll() is None

    assert runtime_server.delete_session(session_id) is True
    assert kernel.process.poll() is not None


def test_runtime_python_sessions_overlap_and_isolate_env_and_output(tmp_path):
    session_a = f"test-a-{uuid4().hex}"
    session_b = f"test-b-{uuid4().hex}"
    runtime_server.get_or_create_session(
        session_a,
        cwd=str(tmp_path / "a"),
        env={"SESSION_SECRET": "alpha"},
    )
    runtime_server.get_or_create_session(
        session_b,
        cwd=str(tmp_path / "b"),
        env={"SESSION_SECRET": "beta"},
    )
    # Warm both child interpreters so the timing assertion measures execution,
    # not process startup variance.
    assert runtime_server.execute_python(session_a, "warm = True")["ok"]
    assert runtime_server.execute_python(session_b, "warm = True")["ok"]

    source = (
        "import os, time\n"
        "secret = os.environ['SESSION_SECRET']\n"
        "time.sleep(0.6)\n"
        "print(secret)\n"
        "secret"
    )
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(runtime_server.execute_python, session_a, source)
        future_b = pool.submit(runtime_server.execute_python, session_b, source)
        result_a = future_a.result(timeout=3)
        result_b = future_b.result(timeout=3)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, "different session kernels should execute concurrently"
    assert result_a["stdout"] == "alpha\n"
    assert result_a["result"] == "'alpha'"
    assert result_b["stdout"] == "beta\n"
    assert result_b["result"] == "'beta'"
    runtime_server.delete_session(session_a)
    runtime_server.delete_session(session_b)


def test_runtime_python_native_output_isolated_across_concurrent_sessions(tmp_path):
    session_a = f"native-a-{uuid4().hex}"
    session_b = f"native-b-{uuid4().hex}"
    runtime_server.get_or_create_session(
        session_a,
        cwd=str(tmp_path / "a"),
        env={"SESSION_MARK": "alpha"},
    )
    runtime_server.get_or_create_session(
        session_b,
        cwd=str(tmp_path / "b"),
        env={"SESSION_MARK": "beta"},
    )
    assert runtime_server.execute_python(session_a, "warm = True")["ok"]
    assert runtime_server.execute_python(session_b, "warm = True")["ok"]

    source = (
        "import os, subprocess, sys, time\n"
        "mark = os.environ['SESSION_MARK']\n"
        "time.sleep(0.6)\n"
        "os.write(1, f'{mark} native\\n'.encode())\n"
        "subprocess.run([sys.executable, '-c', "
        "'import os,sys; os.write(2, sys.argv[1].encode())', "
        "mark + ' child\\n'], check=True)\n"
        "mark"
    )
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(runtime_server.execute_python, session_a, source)
        future_b = pool.submit(runtime_server.execute_python, session_b, source)
        result_a = future_a.result(timeout=3)
        result_b = future_b.result(timeout=3)
    elapsed = time.monotonic() - started

    assert elapsed < 1.1, "different session kernels should execute concurrently"
    assert result_a["stdout"] == "alpha native\n"
    assert result_a["stderr"] == "alpha child\n"
    assert result_a["result"] == "'alpha'"
    assert result_b["stdout"] == "beta native\n"
    assert result_b["stderr"] == "beta child\n"
    assert result_b["result"] == "'beta'"


def test_runtime_python_same_session_is_serial_and_stateful(tmp_path):
    session_id = f"test-{uuid4().hex}"
    runtime_server.get_or_create_session(session_id, cwd=str(tmp_path))
    runtime_server.execute_python(session_id, "events = []")

    def append_after(delay: float, value: str):
        return runtime_server.execute_python(
            session_id,
            f"import time\ntime.sleep({delay})\nevents.append('{value}')\nlist(events)",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(append_after, 0.3, "first")
        time.sleep(0.05)
        second = pool.submit(append_after, 0.0, "second")
        first_result = first.result(timeout=2)
        second_result = second.result(timeout=2)

    assert first_result["result"] == "['first']"
    assert second_result["result"] == "['first', 'second']"
    runtime_server.delete_session(session_id)


def test_runtime_python_timeout_kills_only_that_kernel_and_resets_state(tmp_path):
    timed_session = f"timed-{uuid4().hex}"
    healthy_session = f"healthy-{uuid4().hex}"
    runtime_server.get_or_create_session(timed_session, cwd=str(tmp_path / "timed"))
    runtime_server.get_or_create_session(healthy_session, cwd=str(tmp_path / "healthy"))
    runtime_server.execute_python(timed_session, "sentinel = 42")
    runtime_server.execute_python(healthy_session, "sentinel = 99")
    old_kernel = runtime_server.get_or_create_session(timed_session).python_kernel
    assert old_kernel is not None

    timed_out = runtime_server.execute_python(
        timed_session,
        "import time; time.sleep(30)",
        timeout_seconds=1,
    )
    assert timed_out["ok"] is False
    assert timed_out["error_name"] == "TimeoutError"
    assert old_kernel.process.poll() is not None

    reset = runtime_server.execute_python(timed_session, "sentinel")
    healthy = runtime_server.execute_python(healthy_session, "sentinel")
    assert reset["ok"] is False
    assert reset["error_name"] == "NameError"
    assert healthy["result"] == "99"
    runtime_server.delete_session(timed_session)
    runtime_server.delete_session(healthy_session)


def test_runtime_python_delete_kills_kernel_descendants(tmp_path):
    session_id = f"test-{uuid4().hex}"
    runtime_server.get_or_create_session(session_id, cwd=str(tmp_path))
    result = runtime_server.execute_python(
        session_id,
        "import subprocess\nchild = subprocess.Popen(['sleep', '30'])\nchild.pid",
    )
    assert result["ok"] is True
    child_pid = int(result["result"])

    assert runtime_server.delete_session(session_id) is True
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("session child process survived kernel process-group cleanup")


def test_shell_and_tty_processes_have_isolated_env_and_process_groups(tmp_path):
    session_a = f"shell-a-{uuid4().hex}"
    session_b = f"shell-b-{uuid4().hex}"
    runtime_server.get_or_create_session(
        session_a,
        cwd=str(tmp_path / "a"),
        env={"SESSION_MARK": "alpha"},
    )
    runtime_server.get_or_create_session(
        session_b,
        cwd=str(tmp_path / "b"),
        env={"SESSION_MARK": "beta"},
    )

    process_a = runtime_server.start_interactive_command(
        session_a,
        cmd="printf '%s\\n' \"$SESSION_MARK\"; sleep 30",
        yield_time_ms=100,
    )
    process_b = runtime_server.start_interactive_command(
        session_b,
        cmd="printf '%s\\n' \"$SESSION_MARK\"; sleep 30",
        tty=True,
        yield_time_ms=100,
    )
    assert process_a["completed"] is False
    assert process_b["completed"] is False
    assert "alpha" in process_a["stdout"]
    assert "beta" in process_b["stdout"]
    assert "beta" not in process_a["stdout"]
    assert "alpha" not in process_b["stdout"]

    runtime_a = runtime_server.get_or_create_session(session_a).processes[
        process_a["process_id"]
    ]
    runtime_b = runtime_server.get_or_create_session(session_b).processes[
        process_b["process_id"]
    ]
    assert os.getpgid(runtime_a.popen.pid) == runtime_a.popen.pid
    assert os.getpgid(runtime_b.popen.pid) == runtime_b.popen.pid
    assert runtime_a.popen.pid != runtime_b.popen.pid

    runtime_server.delete_session(session_a)
    assert runtime_a.popen.poll() is not None
    assert runtime_b.popen.poll() is None
    runtime_server.delete_session(session_b)
    assert runtime_b.popen.poll() is not None


def test_runtime_health_stays_responsive_during_python_execution(tmp_path):
    session_id = f"test-{uuid4().hex}"
    marker = tmp_path / "started"
    runtime_server.get_or_create_session(session_id, cwd=str(tmp_path))
    server = runtime_server._RuntimeHTTPServer(  # noqa: SLF001
        ("127.0.0.1", 0), runtime_server.RuntimeHandler
    )
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            executing = pool.submit(
                runtime_server.execute_python,
                session_id,
                f"from pathlib import Path\nimport time\nPath({str(marker)!r}).touch()\ntime.sleep(1)",
            )
            deadline = time.monotonic() + 2
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert marker.exists()

            started = time.monotonic()
            connection = http.client.HTTPConnection(*server.server_address, timeout=1)
            connection.request("GET", "/health")
            response = connection.getresponse()
            body = json.loads(response.read())
            connection.close()
            elapsed = time.monotonic() - started

            assert response.status == 200
            assert body == {"status": "ok"}
            assert elapsed < 0.5
            assert executing.result(timeout=2)["ok"] is True
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
        runtime_server.delete_session(session_id)


@pytest.mark.anyio
async def test_runtime_proxy_forwards_python_timeout_with_termination_grace(
    monkeypatch,
):
    captured: dict[str, object] = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"ok":false,"stderr":"timed out","error_name":"TimeoutError"}'

    def fake_urlopen(req, timeout):
        captured["payload"] = json.loads(req.data)
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(request, "urlopen", fake_urlopen)
    proxy = RuntimeProxy("http://runtime", "sandbox-1")
    response = await proxy.execute_code("pass", 7, "session-1")

    assert captured == {
        "payload": {"code": "pass", "timeout_seconds": 7},
        "timeout": 12,
    }
    assert response[3] == "TimeoutError"


@pytest.mark.anyio
async def test_runtime_post_never_replays_forgeable_e2b_route_miss(monkeypatch):
    calls = 0

    def fake_urlopen(req, timeout):
        nonlocal calls
        del timeout
        calls += 1
        raise error.HTTPError(
            req.full_url,
            502,
            "Bad Gateway",
            {},
            io.BytesIO(
                b'{"sandboxId":"e2b-provider-1",'
                b'"message":"The sandbox was not found","code":502}'
            ),
        )

    monkeypatch.setattr(request, "urlopen", fake_urlopen)

    def request_once(*args, **kwargs):
        return endpoint_transport.request_endpoint_http(
            *args,
            **kwargs,
            retry_seconds=0,
        )

    monkeypatch.setattr(runtime_proxy, "request_endpoint_http", request_once)
    proxy = RuntimeProxy(
        "http://runtime",
        "sandbox-1",
        transient_gateway="e2b",
        instance_id="e2b-provider-1",
        port=8080,
    )

    with pytest.raises(HTTPException) as caught:
        await proxy.execute_code("print('done')", 7, "session-1")

    assert caught.value.status_code == 502
    assert calls == 1


@pytest.mark.anyio
async def test_legacy_runtime_provider_never_refreshes_mutating_route_miss(
    monkeypatch,
):
    provider = _RefreshingE2BRuntimeProvider()
    calls: list[str] = []

    def fake_urlopen(req, timeout):
        del timeout
        calls.append(req.full_url)
        raise error.HTTPError(
            req.full_url,
            502,
            "Bad Gateway",
            {},
            io.BytesIO(
                b'{"sandboxId":"e2b-provider-1",'
                b'"message":"The sandbox was not found","code":502}'
            ),
        )

    def request_once(*args, **kwargs):
        return endpoint_transport.request_endpoint_http(
            *args,
            **kwargs,
            retry_seconds=0,
        )

    monkeypatch.setattr(request, "urlopen", fake_urlopen)
    monkeypatch.setattr(runtime_proxy, "request_endpoint_http", request_once)

    with pytest.raises(HTTPException):
        await provider.execute_code(
            "sandbox-1",
            "session-1",
            "print('done')",
            7,
        )

    assert provider.resolve_calls == 1
    assert provider.refresh_calls == 0
    assert provider.invalidated == []
    assert calls == ["http://runtime-1/sessions/session-1/execute"]


@pytest.mark.anyio
async def test_runtime_proxy_does_not_retry_gateway_shape_without_policy(monkeypatch):
    calls = 0

    def fake_urlopen(req, timeout):
        nonlocal calls
        del timeout
        calls += 1
        raise error.HTTPError(
            req.full_url,
            502,
            "Bad Gateway",
            {},
            io.BytesIO(
                b'{"sandboxId":"e2b-provider-1",'
                b'"message":"The sandbox was not found","code":502}'
            ),
        )

    monkeypatch.setattr(request, "urlopen", fake_urlopen)
    proxy = RuntimeProxy("http://runtime", "sandbox-1")

    with pytest.raises(HTTPException):
        await proxy.execute_code("print('done')", 7, "session-1")

    assert calls == 1


@pytest.mark.anyio
async def test_runtime_port_not_open_post_is_not_replayed(
    monkeypatch,
):
    calls = 0

    def fake_urlopen(req, timeout):
        nonlocal calls
        del timeout
        calls += 1
        raise error.HTTPError(
            req.full_url,
            502,
            "Bad Gateway",
            {},
            io.BytesIO(
                b'{"sandboxId":"e2b-provider-1",'
                b'"message":"The sandbox is running but port is not open",'
                b'"port":8080,"code":502}'
            ),
        )

    monkeypatch.setattr(request, "urlopen", fake_urlopen)

    def request_once(*args, **kwargs):
        return endpoint_transport.request_endpoint_http(
            *args,
            **kwargs,
            retry_seconds=0,
        )

    monkeypatch.setattr(runtime_proxy, "request_endpoint_http", request_once)
    proxy = RuntimeProxy(
        "http://runtime",
        "sandbox-1",
        transient_gateway="e2b",
        instance_id="e2b-provider-1",
        port=8080,
    )

    with pytest.raises(HTTPException) as caught:
        await proxy.execute_code("print('done')", 7, "session-1")

    assert caught.value.status_code == 502
    assert calls == 1


def test_e2b_gateway_retry_can_recover_near_end_of_full_budget(monkeypatch):
    now = 0.0
    calls = 0

    def clock() -> float:
        return now

    def advance(delay: float) -> None:
        nonlocal now
        now += delay

    def fake_urlopen(req, timeout):
        nonlocal calls
        del timeout
        calls += 1
        if now < 14.75:
            raise error.HTTPError(
                req.full_url,
                502,
                "Bad Gateway",
                {},
                io.BytesIO(
                    b'{"sandboxId":"e2b-provider-1",'
                    b'"message":"The sandbox was not found","code":502}'
                ),
            )
        return _FakeUrlResponse(body=b'{"ok":true}')

    monkeypatch.setattr(request, "urlopen", fake_urlopen)
    req = request.Request("https://runtime.e2b.test/health")

    status, _, body = endpoint_transport.request_endpoint_http(
        req,
        timeout=2,
        transient_gateway="e2b",
        expected_instance_id="e2b-provider-1",
        expected_port=8080,
        clock=clock,
        sleep=advance,
        jitter=lambda _start, _end: 0.0,
    )

    assert status == 200
    assert body == b'{"ok":true}'
    assert 14.75 <= now <= 16.0
    assert calls < 20


def test_e2b_gateway_retry_rejects_mismatched_provider_identity(monkeypatch):
    calls = 0

    def fake_urlopen(req, timeout):
        nonlocal calls
        del timeout
        calls += 1
        raise error.HTTPError(
            req.full_url,
            502,
            "Bad Gateway",
            {},
            io.BytesIO(
                b'{"sandboxId":"different-provider",'
                b'"message":"The sandbox was not found","code":502}'
            ),
        )

    monkeypatch.setattr(request, "urlopen", fake_urlopen)
    req = request.Request("https://runtime.e2b.test/health", method="GET")

    status, _, _ = endpoint_transport.request_endpoint_http(
        req,
        timeout=2,
        transient_gateway="e2b",
        expected_instance_id="e2b-provider-1",
        expected_port=8080,
    )

    assert status == 502
    assert calls == 1


def test_e2b_gateway_retry_exhaustion_raises_typed_pre_routing_signal(
    monkeypatch,
):
    calls = 0

    def fake_urlopen(req, timeout):
        nonlocal calls
        del timeout
        calls += 1
        raise error.HTTPError(
            req.full_url,
            502,
            "Bad Gateway",
            {},
            io.BytesIO(
                b'{"sandboxId":"e2b-provider-1",'
                b'"message":"The sandbox is running but port is not open",'
                b'"port":8090,"code":502}'
            ),
        )

    monkeypatch.setattr(request, "urlopen", fake_urlopen)
    req = request.Request("https://runtime.e2b.test/health", method="GET")

    with pytest.raises(endpoint_transport.EndpointRoutingUnavailable) as exc_info:
        endpoint_transport.request_endpoint_http(
            req,
            timeout=2,
            transient_gateway="e2b",
            expected_instance_id="e2b-provider-1",
            expected_port=8090,
            retry_seconds=0,
        )

    assert exc_info.value.instance_id == "e2b-provider-1"
    assert exc_info.value.port == 8090
    assert calls == 1


def test_malformed_runtime_json_is_logged_and_returned_bounded(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=kubernetes.__name__)

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"not json"

    def fake_urlopen(req, timeout):
        del req, timeout
        return _Response()

    monkeypatch.setattr(request, "urlopen", fake_urlopen)
    req = request.Request("http://runtime/sessions/s1/exec-command", method="POST")

    with pytest.raises(HTTPException) as exc_info:
        kubernetes._request_runtime_json(
            req,
            timeout=1,
            operation="process command request",
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["error"] == "runtime returned malformed JSON"
    assert exc_info.value.detail["runtime_body"] == "not json"
    assert "agentbox.kubernetes.runtime_returned_malformed_json.diagnostic" in caplog.text
    assert "not json" not in caplog.text


def test_runtime_handler_rejects_shell_and_login_exec_fields():
    server = ThreadingHTTPServer(("127.0.0.1", 0), runtime_server.RuntimeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for payload in [
            {"cmd": "printf hi", "shell": "/bin/sh"},
            {"cmd": "printf hi", "login": True},
        ]:
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            connection.request(
                "POST",
                "/sessions/s1/exec-command",
                body=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            body = json.loads(response.read().decode("utf-8"))
            connection.close()

            assert response.status == 400
            assert "Unsupported field" in body["detail"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_runtime_handler_logs_and_returns_json_500_for_unhandled_exception(
    monkeypatch,
    caplog,
):
    caplog.set_level(logging.ERROR, logger=runtime_server.__name__)

    def failing_get_or_create_session(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("session exploded " + ("x" * 4000))

    monkeypatch.setattr(
        runtime_server,
        "get_or_create_session",
        failing_get_or_create_session,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), runtime_server.RuntimeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        connection.request(
            "POST",
            "/sessions/s1",
            body=json.dumps({"cwd": "/workspace"}),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        body = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 500
    assert body["detail"]["message"] == "Unhandled runtime server error"
    assert "session exploded" in body["detail"]["error"]
    assert len(body["detail"]["error"]) <= (
        runtime_server.MAX_RUNTIME_RESPONSE_ERROR_LENGTH + len("... [truncated]")
    )
    assert (
        "agentbox.runtime.request_failed"
        in caplog.text
    )
    assert "session exploded" not in caplog.text
