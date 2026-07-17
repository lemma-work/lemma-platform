from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import http.client
import json
import time
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import RedirectResponse, Response

from agentbox.apps import SandboxAppSpec, sandbox_app, sandbox_app_from_slug
from agentbox.auth import require_api_key
from agentbox.config import settings
from agentbox.endpoint_transport import (
    EndpointRoutingUnavailable,
    REQUEST_NOT_DELIVERED_HEADER,
    is_transient_gateway_response,
    request_endpoint_http,
)
from agentbox.lifecycle_manager import SandboxLifecycleManager
from agentbox.providers import SandboxProvider
from agentbox.providers.models import SandboxEndpoint
from agentbox.sandbox_ids import validate_sandbox_id
from agentbox.schemas import AppAccessRequest, AppAccessResponse, SandboxInternalStatus
from agentbox.to_thread import run_sync
from agentbox.state_store.protocol import AsyncStateStore

from .deps import lifecycle_manager, state_store
from .lifecycle import activity_lease

router = APIRouter()

APP_ACCESS_COOKIE_PREFIX = "agentbox_app_access_"
APP_ACCESS_TOKEN_PARAM = "token"
# Per-request override (seconds) for how long the proxy waits on the in-sandbox
# app before timing out. Consumed by the proxy, never forwarded upstream.
UPSTREAM_TIMEOUT_HEADER = "X-Agentbox-Upstream-Timeout"
BROWSER_DASHBOARD_LOCAL_HTTP_ORIGINS = (
    "http://localhost:4848",
    "http://127.0.0.1:4848",
)
BROWSER_DASHBOARD_LOCAL_WS_ORIGINS = (
    "ws://localhost:4848",
    "ws://127.0.0.1:4848",
)
BROWSER_DASHBOARD_FOCUS_STYLE_MARKER = b"agentbox-browser-dashboard-focus-style"
BROWSER_DASHBOARD_FOCUS_STYLE = b"""
<style id="agentbox-browser-dashboard-focus-style">
@media (min-width: 768px) {
  #activity {
    display: none !important;
    flex: 0 0 0 !important;
    width: 0 !important;
    min-width: 0 !important;
    max-width: 0 !important;
  }

  [data-separator]:has(+ #activity) {
    display: none !important;
  }
}
</style>
"""


@router.post(
    "/sandboxes/{sandbox_id}/apps/{app_name}/access",
    response_model=AppAccessResponse,
    dependencies=[Depends(require_api_key)],
)
async def get_sandbox_app_access_url(
    sandbox_id: str,
    app_name: str,
    request: AppAccessRequest,
    store: AsyncStateStore = Depends(state_store),
    manager: SandboxLifecycleManager = Depends(lifecycle_manager),
) -> AppAccessResponse:
    validate_sandbox_id(sandbox_id)
    app_spec = resolve_sandbox_app(app_name)
    if app_spec.exposure != "workspace_user":
        raise HTTPException(
            status_code=404, detail="Sandbox app is not user accessible"
        )
    async with activity_lease(
        store,
        manager,
        sandbox_id,
        session_id=None,
        operation=f"app-access:{app_spec.name}",
    ):
        pass

    expires_at = int(time.time()) + request.ttl_seconds
    token = create_app_access_token(sandbox_id, app_spec.name, expires_at)
    return AppAccessResponse(
        sandbox_id=sandbox_id,
        app=app_spec.name,
        url=sandbox_app_public_url(app_spec, sandbox_id, token),
        expires_at=expires_at,
    )


@router.api_route(
    "/sandboxes/{sandbox_id}/apps/{app_name}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    dependencies=[Depends(require_api_key)],
)
async def proxy_sandbox_app_internal(
    sandbox_id: str,
    app_name: str,
    path: str,
    request: Request,
    store: AsyncStateStore = Depends(state_store),
    manager: SandboxLifecycleManager = Depends(lifecycle_manager),
) -> Response:
    validate_sandbox_id(sandbox_id)
    app_spec = resolve_sandbox_app(app_name)
    if app_spec.auth_mode != "manager_api_key":
        raise HTTPException(status_code=404, detail="Sandbox app is not private")
    async with activity_lease(
        store,
        manager,
        sandbox_id,
        session_id=None,
        operation=f"http:{app_spec.name}",
    ) as lease:
        return await proxy_sandbox_app_http_request(
            app_spec,
            sandbox_id,
            path,
            request,
            manager,
            sandbox_generation=lease.sandbox_generation,
            forward_authorization=True,
        )


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def proxy_sandbox_app_public_host(
    request: Request,
    path: str,
    store: AsyncStateStore = Depends(state_store),
    manager: SandboxLifecycleManager = Depends(lifecycle_manager),
) -> Response:
    target = sandbox_app_from_request_host(request)
    if target is None:
        raise HTTPException(status_code=404, detail="Not found")
    app_spec, sandbox_id = target
    token = validate_sandbox_app_access(app_spec, sandbox_id, request)
    if (
        token
        and request.query_params.get(APP_ACCESS_TOKEN_PARAM)
        and app_access_should_redirect_to_cookie()
    ):
        return app_access_redirect_response(request, token)

    async with activity_lease(
        store,
        manager,
        sandbox_id,
        session_id=None,
        operation=f"public-http:{app_spec.name}",
    ) as lease:
        response = await proxy_sandbox_app_http_request(
            app_spec,
            sandbox_id,
            path,
            request,
            manager,
            sandbox_generation=lease.sandbox_generation,
            access_token=token,
        )
    if token:
        set_app_access_cookie(response, app_spec, sandbox_id, token)
    return response


@router.websocket("/{path:path}")
async def proxy_sandbox_app_public_websocket(
    websocket: WebSocket,
    path: str,
    store: AsyncStateStore = Depends(state_store),
    manager: SandboxLifecycleManager = Depends(lifecycle_manager),
) -> None:
    target = sandbox_app_from_host(websocket.headers.get("host") or "")
    if target is None:
        await websocket.close(code=1008)
        return
    app_spec, sandbox_id = target
    if not validate_sandbox_app_websocket_access(app_spec, sandbox_id, websocket):
        await websocket.close(code=1008)
        return
    try:
        async with activity_lease(
            store,
            manager,
            sandbox_id,
            session_id=None,
            operation=f"websocket:{app_spec.name}",
        ) as lease:
            await proxy_sandbox_app_websocket_request(
                app_spec,
                sandbox_id,
                path,
                websocket,
                manager,
                sandbox_generation=lease.sandbox_generation,
            )
    except HTTPException:
        await websocket.close(code=1011)


def resolve_sandbox_app(app_name: str) -> SandboxAppSpec:
    try:
        return sandbox_app(app_name)
    except ValueError:
        try:
            return sandbox_app_from_slug(app_name)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="Sandbox app not found"
            ) from exc


def sandbox_app_public_url(
    app_spec: SandboxAppSpec,
    sandbox_id: str,
    token: str,
) -> str:
    host = sandbox_app_public_host(app_spec, sandbox_id)
    parsed_api_url = urlparse.urlparse(settings.agentbox_api_url)
    scheme = parsed_api_url.scheme or "https"
    return f"{scheme}://{host}/?{APP_ACCESS_TOKEN_PARAM}={urlparse.quote(token)}"


def sandbox_app_public_host(app_spec: SandboxAppSpec, sandbox_id: str) -> str:
    app_domain = sandbox_app_domain()
    return f"{validate_sandbox_id(sandbox_id)}-{app_spec.public_slug}.{app_domain}"


def sandbox_app_domain() -> str:
    app_domain = settings.agentbox_app_domain
    if app_domain:
        return app_domain

    parsed = urlparse.urlparse(settings.agentbox_api_url)
    if not parsed.netloc:
        raise RuntimeError("AGENTBOX_APP_DOMAIN or AGENTBOX_API_URL host is required")

    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port is not None else ""
    if host in {"127.0.0.1", "0.0.0.0", "localhost", "::1"}:
        return f"127-0-0-1.sslip.io{port}"
    return parsed.netloc


def sandbox_app_from_request_host(
    request: Request,
) -> tuple[SandboxAppSpec, str] | None:
    return sandbox_app_from_host(request.headers.get("host") or "")


def sandbox_app_from_host(host: str) -> tuple[SandboxAppSpec, str] | None:
    app_domain = sandbox_app_domain()
    host_without_port = host.split(":", 1)[0]
    domain_without_port = app_domain.split(":", 1)[0]
    suffix = f".{domain_without_port}"
    if not host_without_port.endswith(suffix):
        return None
    label = host_without_port[: -len(suffix)]
    sandbox_id, separator, app_slug = label.rpartition("-")
    if not separator or not sandbox_id or not app_slug:
        return None
    try:
        app_spec = sandbox_app_from_slug(app_slug)
        return app_spec, validate_sandbox_id(sandbox_id)
    except ValueError:
        return None


def app_access_cookie_name(app_name: str, sandbox_id: str) -> str:
    return f"{APP_ACCESS_COOKIE_PREFIX}{app_name}_{sandbox_id}"


def create_app_access_token(
    sandbox_id: str,
    app_name: str,
    expires_at: int,
) -> str:
    payload = json.dumps(
        {
            "typ": "workspace_app_access",
            "sandbox_id": sandbox_id,
            "app": app_name,
            "expires_at": expires_at,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded_payload = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(
        settings.agentbox_api_key.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{encoded_payload}.{encoded_signature}"


def decode_app_access_token_payload(token: str | None) -> dict[str, object] | None:
    if not token or "." not in token:
        return None
    encoded_payload, encoded_signature = token.rsplit(".", 1)
    expected_signature = hmac.new(
        settings.agentbox_api_key.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    try:
        signature = base64.urlsafe_b64decode(
            encoded_signature + "=" * (-len(encoded_signature) % 4)
        )
        payload = json.loads(
            base64.urlsafe_b64decode(
                encoded_payload + "=" * (-len(encoded_payload) % 4)
            )
        )
    except Exception:
        return None
    if not hmac.compare_digest(signature, expected_signature):
        return None
    return payload if isinstance(payload, dict) else None


def validate_app_access_token(
    sandbox_id: str,
    app_name: str,
    token: str | None,
) -> bool:
    payload = decode_app_access_token_payload(token)
    return bool(
        payload
        and payload.get("typ") == "workspace_app_access"
        and payload.get("sandbox_id") == sandbox_id
        and payload.get("app") == app_name
        and isinstance(payload.get("expires_at"), int)
        and payload["expires_at"] >= int(time.time())
    )


def validate_sandbox_app_access(
    app_spec: SandboxAppSpec,
    sandbox_id: str,
    request: Request,
) -> str | None:
    if app_spec.auth_mode != "workspace_access_token":
        raise HTTPException(status_code=404, detail="Sandbox app is not public")
    token = request.query_params.get(APP_ACCESS_TOKEN_PARAM)
    if validate_app_access_token(sandbox_id, app_spec.name, token):
        return token
    cookie_token = request.cookies.get(
        app_access_cookie_name(app_spec.name, sandbox_id)
    )
    if validate_app_access_token(sandbox_id, app_spec.name, cookie_token):
        return None
    referer_token = app_access_token_from_referer(request, app_spec, sandbox_id)
    if validate_app_access_token(sandbox_id, app_spec.name, referer_token):
        return referer_token
    raise HTTPException(
        status_code=403, detail="Sandbox app access token is invalid or expired"
    )


def validate_sandbox_app_websocket_access(
    app_spec: SandboxAppSpec,
    sandbox_id: str,
    websocket: WebSocket,
) -> bool:
    token = websocket.query_params.get(APP_ACCESS_TOKEN_PARAM)
    if validate_app_access_token(sandbox_id, app_spec.name, token):
        return True
    cookie_token = websocket.cookies.get(
        app_access_cookie_name(app_spec.name, sandbox_id)
    )
    return validate_app_access_token(sandbox_id, app_spec.name, cookie_token)


def app_access_token_from_referer(
    request: Request,
    app_spec: SandboxAppSpec,
    sandbox_id: str,
) -> str | None:
    referer = request.headers.get("referer")
    if not referer:
        return None
    parsed = urlparse.urlparse(referer)
    if not parsed.netloc:
        return None
    target = sandbox_app_from_host(parsed.netloc)
    if target is None:
        return None
    referer_app_spec, referer_sandbox_id = target
    if referer_app_spec.name != app_spec.name or referer_sandbox_id != sandbox_id:
        return None
    values = urlparse.parse_qs(parsed.query).get(APP_ACCESS_TOKEN_PARAM) or []
    return values[0] if values else None


def app_access_should_redirect_to_cookie() -> bool:
    return urlparse.urlparse(settings.agentbox_api_url).scheme == "https"


def app_access_redirect_response(request: Request, token: str) -> RedirectResponse:
    query_pairs = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key != APP_ACCESS_TOKEN_PARAM
    ]
    query = urlparse.urlencode(query_pairs, doseq=True)
    suffix = f"?{query}" if query else ""
    response = RedirectResponse(url=f"{request.url.path}{suffix}", status_code=307)
    target = sandbox_app_from_request_host(request)
    if target is not None:
        app_spec, sandbox_id = target
        set_app_access_cookie(response, app_spec, sandbox_id, token)
    return response


def set_app_access_cookie(
    response: Response,
    app_spec: SandboxAppSpec,
    sandbox_id: str,
    token: str,
) -> None:
    payload = decode_app_access_token_payload(token)
    expires_at = payload.get("expires_at") if payload else None
    max_age = 600
    if isinstance(expires_at, int):
        max_age = max(0, expires_at - int(time.time()))
    secure_cookie = urlparse.urlparse(settings.agentbox_api_url).scheme == "https"
    response.set_cookie(
        app_access_cookie_name(app_spec.name, sandbox_id),
        token,
        httponly=True,
        secure=secure_cookie,
        samesite="none" if secure_cookie else "lax",
        max_age=max_age,
        path="/",
    )


def sandbox_app_upstream_base_url(
    status_obj: SandboxInternalStatus,
    app_spec: SandboxAppSpec,
) -> str | None:
    app_status = status_obj.apps.get(app_spec.name)
    if app_status and app_status.private_url:
        return app_status.private_url.rstrip("/")
    if app_spec.name == "runtime" and status_obj.runtime_url:
        return status_obj.runtime_url.rstrip("/")
    if status_obj.pod_ip:
        return f"http://{status_obj.pod_ip}:{app_spec.port}"
    return None


async def resolve_sandbox_app_endpoint(
    provider: SandboxProvider,
    sandbox_id: str,
    app_spec: SandboxAppSpec,
    *,
    status_obj: SandboxInternalStatus | None = None,
    protocol: str = "http",
) -> SandboxEndpoint:
    """Resolve provider authentication without coupling the proxy to its SDK."""

    resolver = getattr(provider, "resolve_endpoint", None)
    if resolver is not None:
        return await resolver(sandbox_id, app_spec, protocol=protocol)
    # Transitional fallback for external providers implementing the historical
    # status-only contract. This can be removed after one deprecation cycle.
    status_obj = status_obj or await provider.get_status(sandbox_id)
    base_url = sandbox_app_upstream_base_url(status_obj, app_spec)
    if base_url is None:
        raise HTTPException(status_code=409, detail="Sandbox app endpoint is missing")
    return SandboxEndpoint(base_url=base_url)


async def wait_until_sandbox_app_ready(
    app_spec: SandboxAppSpec,
    sandbox_id: str,
    upstream_base_url: str,
    request: Request,
    *,
    endpoint_headers: dict[str, str] | None = None,
    instance_id: str | None = None,
    transient_gateway: str | None = None,
) -> None:
    if not app_spec.health_path:
        return
    ready_cache = getattr(request.app.state, "sandbox_app_ready_cache", None)
    cache_key = (
        (sandbox_id, app_spec.name, upstream_base_url, instance_id)
        if instance_id is not None
        else (sandbox_id, app_spec.name, upstream_base_url)
    )
    if ready_cache is not None and cache_key in ready_cache:
        return

    deadline = time.monotonic() + settings.agentbox_sandbox_app_ready_timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if await run_sync(
                check_sandbox_app_health,
                upstream_base_url,
                app_spec,
                endpoint_headers,
                transient_gateway,
                instance_id,
            ):
                if ready_cache is not None:
                    ready_cache.add(cache_key)
                return
        except (urlerror.URLError, http.client.HTTPException, OSError) as exc:
            last_error = exc
        await asyncio.sleep(0.25)

    detail = (
        f"Sandbox app {app_spec.name} did not become ready before timeout"
        f"{': ' + str(last_error) if last_error else ''}"
    )
    raise HTTPException(status_code=504, detail=detail)


def check_sandbox_app_health(
    upstream_base_url: str,
    app_spec: SandboxAppSpec,
    headers: dict[str, str] | None = None,
    transient_gateway: str | None = None,
    instance_id: str | None = None,
) -> bool:
    path = (
        app_spec.health_path
        if app_spec.health_path.startswith("/")
        else f"/{app_spec.health_path}"
    )
    req = urlrequest.Request(
        f"{upstream_base_url.rstrip('/')}{path}",
        headers=headers or {},
        method="GET",
    )
    status_code, _, _ = request_endpoint_http(
        req,
        timeout=2,
        transient_gateway=transient_gateway,
        expected_instance_id=instance_id,
        expected_port=app_spec.port,
    )
    return 200 <= status_code < 300


def resolve_upstream_timeout(request: Request) -> float:
    """Resolve how long the proxy waits on the in-sandbox app for this request.

    Callers that legitimately need longer than the interactive default (e.g. a
    synchronous function execute that runs for minutes) set the
    ``X-Agentbox-Upstream-Timeout`` header in seconds; it is clamped to
    ``(0, agentbox_app_proxy_max_timeout_seconds]``. An absent or invalid header
    falls back to ``agentbox_app_proxy_timeout_seconds``.
    """
    raw = request.headers.get(UPSTREAM_TIMEOUT_HEADER)
    if raw is None:
        return settings.agentbox_app_proxy_timeout_seconds
    try:
        requested = float(raw)
    except (TypeError, ValueError):
        return settings.agentbox_app_proxy_timeout_seconds
    if requested <= 0:
        return settings.agentbox_app_proxy_timeout_seconds
    return min(requested, settings.agentbox_app_proxy_max_timeout_seconds)


def _is_timeout_error(exc: BaseException) -> bool:
    # socket.timeout is an alias of TimeoutError since Python 3.10; urllib also
    # wraps it as URLError(reason=TimeoutError) on connect/read timeouts.
    if isinstance(exc, TimeoutError):
        return True
    return isinstance(getattr(exc, "reason", None), TimeoutError)


async def proxy_sandbox_app_http_request(
    app_spec: SandboxAppSpec,
    sandbox_id: str,
    path: str,
    incoming_request: Request,
    manager: SandboxLifecycleManager | SandboxProvider,
    *,
    sandbox_generation: int | None = None,
    forward_authorization: bool = False,
    access_token: str | None = None,
    _endpoint_refresh_attempted: bool = False,
    _endpoint_override: SandboxEndpoint | None = None,
) -> Response:
    lifecycle_manager = (
        manager
        if hasattr(manager, "database_endpoint")
        and hasattr(manager, "signal_route_failure")
        else None
    )
    legacy_provider = manager if lifecycle_manager is None else None
    endpoint = _endpoint_override
    if endpoint is None:
        if lifecycle_manager is not None:
            endpoint = await lifecycle_manager.database_endpoint(
                sandbox_id,
                app_spec.name,
                expected_generation=sandbox_generation,
            )
        else:
            endpoint = await resolve_sandbox_app_endpoint(
                legacy_provider,
                sandbox_id,
                app_spec,
            )
    upstream_base_url = endpoint.base_url.rstrip("/")
    if upstream_base_url is None:
        raise HTTPException(status_code=409, detail="Sandbox app endpoint is missing")
    if legacy_provider is not None:
        try:
            await wait_until_sandbox_app_ready(
                app_spec,
                sandbox_id,
                upstream_base_url,
                incoming_request,
                endpoint_headers=dict(endpoint.headers),
                instance_id=endpoint.instance_id,
                transient_gateway=endpoint.transient_gateway,
            )
        except EndpointRoutingUnavailable as exc:
            return await retry_sandbox_app_http_after_route_refresh(
                app_spec,
                sandbox_id,
                path,
                incoming_request,
                legacy_provider,
                exc,
                endpoint,
                forward_authorization=forward_authorization,
                access_token=access_token,
                endpoint_refresh_attempted=_endpoint_refresh_attempted,
            )
    query_pairs = [
        (key, value)
        for key, value in incoming_request.query_params.multi_items()
        if key != APP_ACCESS_TOKEN_PARAM
    ]
    query = urlparse.urlencode(query_pairs, doseq=True)
    upstream_path = "/" + path if path else "/"
    upstream_url = f"{upstream_base_url}{upstream_path}{'?' + query if query else ''}"
    body = await incoming_request.body()
    upstream_timeout = resolve_upstream_timeout(incoming_request)
    headers = {
        key: value
        for key, value in incoming_request.headers.items()
        if key.lower()
        not in {
            "cookie",
            "host",
            "content-length",
            "connection",
            "accept-encoding",
            "origin",
            "referer",
            "x-api-key",
            UPSTREAM_TIMEOUT_HEADER.lower(),
        }
        and key.lower() != "authorization"
    }
    caller_authorization = incoming_request.headers.get("authorization")
    endpoint_uses_authorization = any(
        key.lower() == "authorization" for key in endpoint.headers
    )
    if forward_authorization and caller_authorization:
        authorization_header = (
            "X-Agentbox-Forwarded-Authorization"
            if endpoint_uses_authorization
            else "Authorization"
        )
        headers[authorization_header] = caller_authorization
    headers.update(endpoint.headers)
    headers["Accept-Encoding"] = "identity"
    upstream_origin = upstream_base_url.rstrip("/")
    if incoming_request.headers.get("origin"):
        headers["Origin"] = upstream_origin
    if incoming_request.headers.get("referer"):
        headers["Referer"] = (
            f"{upstream_origin}{upstream_path}{'?' + query if query else ''}"
        )

    def _request() -> tuple[int, dict[str, str], bytes]:
        req = urlrequest.Request(
            upstream_url,
            data=body if body else None,
            headers=headers,
            method=incoming_request.method,
        )
        try:
            request_kwargs = {
                "timeout": upstream_timeout,
                "transient_gateway": endpoint.transient_gateway,
                "expected_instance_id": endpoint.instance_id,
                "expected_port": app_spec.port,
            }
            if lifecycle_manager is not None and incoming_request.method.upper() not in {
                "GET",
                "HEAD",
                "OPTIONS",
                "TRACE",
            }:
                # A mutating request may have reached a user-controlled app,
                # so it is never replayed implicitly. Idempotent reads retain
                # the bounded provider-route retry budget; this absorbs E2B
                # route propagation without involving lifecycle control.
                request_kwargs["retry_seconds"] = 0
            return request_endpoint_http(req, **request_kwargs)
        except (urlerror.URLError, http.client.HTTPException, OSError) as exc:
            # An upstream read timeout is distinct from an unreachable app: the
            # request reached the in-sandbox app and it did not respond in time
            # (e.g. a synchronous function still running). Surface it as 504 so
            # callers can treat it as a non-retryable timeout rather than a
            # retryable "app not ready" 502 (re-running a non-idempotent execute).
            if _is_timeout_error(exc):
                raise HTTPException(
                    status_code=504,
                    detail=(
                        f"Sandbox app upstream timed out after "
                        f"{upstream_timeout:.0f}s: {exc}"
                    ),
                ) from exc
            raise HTTPException(
                status_code=502,
                detail=f"Sandbox app proxy failed: {exc}",
            ) from exc

    try:
        status_code, response_headers, content = await run_sync(_request)
    except EndpointRoutingUnavailable as exc:
        if legacy_provider is not None:
            return await retry_sandbox_app_http_after_route_refresh(
                app_spec,
                sandbox_id,
                path,
                incoming_request,
                legacy_provider,
                exc,
                endpoint,
                forward_authorization=forward_authorization,
                access_token=access_token,
                endpoint_refresh_attempted=_endpoint_refresh_attempted,
            )
        assert lifecycle_manager is not None
        await lifecycle_manager.signal_route_failure(
            sandbox_id,
            generation=sandbox_generation,
            reason="provider_gateway_routing",
        )
        raise HTTPException(
            status_code=503,
            headers={
                "Retry-After": "1",
                REQUEST_NOT_DELIVERED_HEADER: "true",
            },
            detail={
                "message": "Sandbox endpoint routing is temporarily unavailable",
                "code": "endpoint_routing_unavailable",
                "retryable": True,
                "provider_id": exc.instance_id,
            },
        ) from exc
    except HTTPException as exc:
        if exc.status_code == 502:
            if legacy_provider is not None:
                invalidate_provider_endpoint_cache(legacy_provider, sandbox_id)
            else:
                assert lifecycle_manager is not None
                await lifecycle_manager.signal_route_failure(
                    sandbox_id,
                    generation=sandbox_generation,
                    reason="endpoint_connect_failure",
                )
        raise
    content, response_headers = rewrite_sandbox_app_response(
        app_spec,
        sandbox_id,
        incoming_request,
        response_headers,
        content,
        access_token=access_token,
    )
    filtered_headers = {
        key: value
        for key, value in response_headers.items()
        if key.lower()
        not in {
            "content-length",
            "transfer-encoding",
            "connection",
            "content-encoding",
            REQUEST_NOT_DELIVERED_HEADER.lower(),
        }
    }
    return Response(content=content, status_code=status_code, headers=filtered_headers)


async def retry_sandbox_app_http_after_route_refresh(
    app_spec: SandboxAppSpec,
    sandbox_id: str,
    path: str,
    incoming_request: Request,
    provider: SandboxProvider,
    routing_error: EndpointRoutingUnavailable,
    endpoint: SandboxEndpoint,
    *,
    forward_authorization: bool,
    access_token: str | None,
    endpoint_refresh_attempted: bool,
) -> Response:
    """Refresh one proven stale provider route and replay within one budget."""

    if endpoint_refresh_attempted:
        raise HTTPException(
            status_code=503,
            headers={
                "Retry-After": "1",
                REQUEST_NOT_DELIVERED_HEADER: "true",
            },
            detail={
                "message": "Sandbox endpoint routing is temporarily unavailable",
                "code": "endpoint_routing_unavailable",
                "retryable": True,
                "provider_id": routing_error.instance_id,
            },
        ) from routing_error
    invalidate_sandbox_app_ready_cache(
        incoming_request,
        sandbox_id,
        app_spec,
        endpoint.base_url.rstrip("/"),
        endpoint.instance_id,
    )
    refreshed_endpoint = await refresh_sandbox_app_endpoint(
        provider,
        sandbox_id,
        app_spec,
        instance_id=routing_error.instance_id,
    )
    return await proxy_sandbox_app_http_request(
        app_spec,
        sandbox_id,
        path,
        incoming_request,
        provider,
        forward_authorization=forward_authorization,
        access_token=access_token,
        _endpoint_refresh_attempted=True,
        _endpoint_override=refreshed_endpoint,
    )


def rewrite_sandbox_app_response(
    app_spec: SandboxAppSpec,
    sandbox_id: str,
    incoming_request: Request,
    response_headers: dict[str, str],
    content: bytes,
    *,
    access_token: str | None = None,
) -> tuple[bytes, dict[str, str]]:
    if app_spec.name != "browser":
        return content, response_headers

    content_type = response_header_value(response_headers, "content-type")
    if not response_content_type_is_rewritable(content_type):
        return content, response_headers

    host = incoming_request.headers.get("host")
    if not host:
        return content, response_headers

    scheme = incoming_request.url.scheme or "http"
    public_http_origin = f"{scheme}://{host}"
    public_ws_scheme = "wss" if scheme == "https" else "ws"
    public_ws_origin = f"{public_ws_scheme}://{host}"

    rewritten = content
    for local_origin in BROWSER_DASHBOARD_LOCAL_HTTP_ORIGINS:
        rewritten = rewritten.replace(
            local_origin.encode("utf-8"),
            public_http_origin.encode("utf-8"),
        )
        rewritten = rewritten.replace(
            json.dumps(local_origin).strip('"').encode("utf-8"),
            json.dumps(public_http_origin).strip('"').encode("utf-8"),
        )
    for local_origin in BROWSER_DASHBOARD_LOCAL_WS_ORIGINS:
        rewritten = rewritten.replace(
            local_origin.encode("utf-8"),
            public_ws_origin.encode("utf-8"),
        )
        rewritten = rewritten.replace(
            json.dumps(local_origin).strip('"').encode("utf-8"),
            json.dumps(public_ws_origin).strip('"').encode("utf-8"),
        )

    if validate_app_access_token(sandbox_id, app_spec.name, access_token):
        rewritten = rewrite_browser_dashboard_websocket_token(
            rewritten, access_token or ""
        )

    if response_content_type_is_html(content_type):
        rewritten = inject_browser_dashboard_focus_style(rewritten)

    if rewritten == content:
        return content, response_headers

    return rewritten, remove_response_headers(response_headers, {"etag"})


def rewrite_browser_dashboard_websocket_token(content: bytes, token: str) -> bytes:
    quoted_token = urlparse.quote(token)
    return content.replace(
        b"/api/session/${e}/stream",
        f"/api/session/${{e}}/stream?{APP_ACCESS_TOKEN_PARAM}={quoted_token}".encode(
            "utf-8"
        ),
    )


def inject_browser_dashboard_focus_style(content: bytes) -> bytes:
    if BROWSER_DASHBOARD_FOCUS_STYLE_MARKER in content:
        return content
    if b"</head>" in content:
        return content.replace(
            b"</head>", BROWSER_DASHBOARD_FOCUS_STYLE + b"</head>", 1
        )
    return BROWSER_DASHBOARD_FOCUS_STYLE + content


def response_header_value(headers: dict[str, str], name: str) -> str:
    lower_name = name.lower()
    for key, value in headers.items():
        if key.lower() == lower_name:
            return value
    return ""


def response_content_type_is_rewritable(content_type: str) -> bool:
    normalized = content_type.lower()
    return any(
        marker in normalized
        for marker in (
            "javascript",
            "text/html",
            "text/plain",
            "application/json",
            "text/css",
            "text/x-component",
        )
    )


def response_content_type_is_html(content_type: str) -> bool:
    return "text/html" in content_type.lower()


def remove_response_headers(
    headers: dict[str, str],
    names: set[str],
) -> dict[str, str]:
    return {key: value for key, value in headers.items() if key.lower() not in names}


def sandbox_app_upstream_websocket_url(
    status_obj: SandboxInternalStatus,
    app_spec: SandboxAppSpec,
    path: str,
    query: str,
) -> str | None:
    base_url = sandbox_app_upstream_base_url(status_obj, app_spec)
    if base_url is None:
        return None
    parsed = urlparse.urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    upstream_path = f"/{path}" if path else "/"
    query_pairs = [
        (key, value)
        for key, value in urlparse.parse_qsl(query, keep_blank_values=True)
        if key != APP_ACCESS_TOKEN_PARAM
    ]
    upstream_query = urlparse.urlencode(query_pairs, doseq=True)
    return f"{scheme}://{parsed.netloc}{upstream_path}{'?' + upstream_query if upstream_query else ''}"


def sandbox_app_upstream_websocket_url_from_endpoint(
    endpoint: SandboxEndpoint,
    path: str,
    query: str,
) -> str:
    parsed = urlparse.urlparse(endpoint.url(protocol="websocket"))
    upstream_path = f"/{path}" if path else "/"
    query_pairs = [
        (key, value)
        for key, value in urlparse.parse_qsl(query, keep_blank_values=True)
        if key != APP_ACCESS_TOKEN_PARAM
    ]
    query_pairs.extend(endpoint.websocket_query.items())
    upstream_query = urlparse.urlencode(query_pairs, doseq=True)
    return (
        f"{parsed.scheme}://{parsed.netloc}{upstream_path}"
        f"{'?' + upstream_query if upstream_query else ''}"
    )


async def proxy_sandbox_app_websocket_request(
    app_spec: SandboxAppSpec,
    sandbox_id: str,
    path: str,
    websocket: WebSocket,
    manager: SandboxLifecycleManager,
    *,
    sandbox_generation: int,
) -> None:
    try:
        endpoint = await manager.database_endpoint(
            sandbox_id,
            app_spec.name,
            expected_generation=sandbox_generation,
        )
    except HTTPException:
        await websocket.close(code=1011)
        return
    try:
        import websockets
    except ImportError:
        await websocket.close(code=1011)
        return
    try:
        upstream, endpoint = await connect_sandbox_app_websocket(
            websockets,
            sandbox_id,
            app_spec,
            path,
            websocket.url.query,
            endpoint,
        )
    except EndpointRoutingUnavailable:
        await manager.signal_route_failure(
            sandbox_id,
            generation=sandbox_generation,
            reason="websocket_provider_gateway_routing",
        )
        await websocket.close(code=1013)
        return
    except Exception:
        await manager.signal_route_failure(
            sandbox_id,
            generation=sandbox_generation,
            reason="websocket_connect_failure",
        )
        await websocket.close(code=1011)
        return
    try:
        # Establish the provider route before accepting the downstream socket;
        # this prevents clients from seeing a successful upgrade followed by
        # an immediate close during E2B route propagation.
        await websocket.accept()
        await relay_app_websocket(websocket, upstream)
    finally:
        await upstream.close()


async def connect_sandbox_app_websocket(
    websockets_module,
    sandbox_id: str,
    app_spec: SandboxAppSpec,
    path: str,
    query: str,
    endpoint: SandboxEndpoint,
):
    """Open one websocket against the DB-published exact provider route."""

    upstream_url = sandbox_app_upstream_websocket_url_from_endpoint(
        endpoint,
        path,
        query,
    )
    try:
        upstream = await websockets_module.connect(
            upstream_url,
            additional_headers=dict(endpoint.headers),
            subprotocols=list(endpoint.websocket_subprotocols) or None,
        )
    except Exception as exc:
        routing_error = websocket_endpoint_routing_error(
            exc,
            endpoint,
            expected_port=app_spec.port,
        )
        if routing_error is not None:
            raise routing_error from exc
        raise
    return upstream, endpoint


def websocket_endpoint_routing_error(
    exc: BaseException,
    endpoint: SandboxEndpoint,
    *,
    expected_port: int,
) -> EndpointRoutingUnavailable | None:
    """Convert only an exact E2B websocket handshake miss to a typed signal."""

    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    body = getattr(response, "body", b"")
    if not isinstance(status_code, int) or not isinstance(body, (bytes, bytearray)):
        return None
    if not is_transient_gateway_response(
        endpoint.transient_gateway,
        status_code,
        bytes(body),
        expected_instance_id=endpoint.instance_id,
        expected_port=expected_port,
    ):
        return None
    if endpoint.transient_gateway is None or endpoint.instance_id is None:
        return None
    return EndpointRoutingUnavailable(
        gateway=endpoint.transient_gateway,
        instance_id=endpoint.instance_id,
        port=expected_port,
    )


def invalidate_provider_sandbox_cache(
    provider: SandboxProvider, sandbox_id: str
) -> None:
    invalidate = getattr(provider, "invalidate_sandbox_cache", None)
    if invalidate is not None:
        invalidate(sandbox_id)


def invalidate_provider_endpoint_cache(
    provider: SandboxProvider, sandbox_id: str
) -> None:
    """Drop a stale SDK connection while preserving provider identity if possible."""

    invalidate = getattr(provider, "invalidate_endpoint_cache", None)
    if invalidate is not None:
        invalidate(sandbox_id)
        return
    invalidate_provider_sandbox_cache(provider, sandbox_id)


def invalidate_sandbox_app_ready_cache(
    request: Request,
    sandbox_id: str,
    app_spec: SandboxAppSpec,
    upstream_base_url: str,
    instance_id: str | None,
) -> None:
    ready_cache = getattr(request.app.state, "sandbox_app_ready_cache", None)
    if ready_cache is None:
        return
    ready_cache.discard((sandbox_id, app_spec.name, upstream_base_url, instance_id))
    # Compatibility with caches populated before endpoint generations were
    # included in the key.
    ready_cache.discard((sandbox_id, app_spec.name, upstream_base_url))


async def refresh_sandbox_app_endpoint(
    provider: SandboxProvider,
    sandbox_id: str,
    app_spec: SandboxAppSpec,
    *,
    instance_id: str,
    protocol: str = "http",
) -> SandboxEndpoint | None:
    refresh = getattr(provider, "refresh_endpoint", None)
    if refresh is not None:
        return await refresh(
            sandbox_id,
            app_spec,
            instance_id=instance_id,
            protocol=protocol,
        )
    # Compatibility fallback for providers which only expose cache
    # invalidation. E2B implements the direct refresh hook so its recovery does
    # not depend on eventually-consistent inventory.
    invalidate_provider_sandbox_cache(provider, sandbox_id)
    return None


async def relay_app_websocket(websocket: WebSocket, upstream) -> None:
    client_disconnected = asyncio.Event()

    async def client_to_upstream() -> None:
        while True:
            message = await websocket.receive()
            if "text" in message:
                await upstream.send(message["text"])
            elif "bytes" in message:
                await upstream.send(message["bytes"])
            elif message.get("type") == "websocket.disconnect":
                client_disconnected.set()
                await upstream.close()
                return

    async def upstream_to_client() -> None:
        from websockets.exceptions import ConnectionClosed

        try:
            async for message in upstream:
                if client_disconnected.is_set():
                    return
                try:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)
                except RuntimeError:
                    # The downstream can close between the check above and the
                    # ASGI send. That is a normal WebSocket race, not an
                    # upstream sandbox failure.
                    if client_disconnected.is_set():
                        return
                    raise
        except ConnectionClosed:
            # A browser or E2B proxy may omit the reciprocal close frame after
            # we send a normal close. Either way the relay is finished; do not
            # turn transport teardown into an ASGI application error.
            return

    tasks = [
        asyncio.create_task(client_to_upstream()),
        asyncio.create_task(upstream_to_client()),
    ]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, asyncio.CancelledError):
            continue
        if isinstance(result, BaseException):
            raise result
