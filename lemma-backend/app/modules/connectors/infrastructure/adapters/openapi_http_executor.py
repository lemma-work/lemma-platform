"""Package-free HTTP executor for OpenAPI-driven connector operations.

Given an operation's ``execution`` descriptor (built at import time by
``infrastructure/openapi/spec_import.py``), the operation payload, and the
connected account's credentials, this builds and sends a single ``httpx``
request and returns either a JSON body or a ``BinaryContentResult``.

It is a **pure HTTP** component: file inputs arrive already resolved to bytes /
base64 / text (pod-datastore paths are resolved upstream in the service layer),
and binary responses are returned as ``BinaryContentResult`` for the service to
optionally persist. It has no database, pod, or datastore dependency.
"""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from lemma_connectors.core.results import BinaryContentResult

from app.core.log.log import get_logger

logger = get_logger(__name__)

# Guard against pathological in-memory payloads (no streaming by design).
_MAX_FILE_BYTES = 100 * 1024 * 1024
_DEFAULT_USER_AGENT = "lemma-connectors"
_DEFAULT_TIMEOUT_SECONDS = 60.0


class OpenApiHttpExecutionError(Exception):
    """HTTP-layer failure carrying status + provider detail.

    Shaped so ``LemmaOperationGateway._translate_execution_error`` classifies it
    like the vendored-package execution path (reads ``status_code`` + ``details``).
    """

    def __init__(self, message: str, *, status_code: int | None = None, details: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _coerce_file_bytes(value: Any) -> tuple[bytes, str | None]:
    """Resolve a file argument to raw bytes.

    Accepts raw bytes (already resolved upstream), ``{"base64": ...}``,
    ``{"text": ...}``, or a plain string. A leftover ``{"pod_path": ...}`` means
    the datastore pre-resolution did not run (no pod context) — surface clearly.
    """
    if isinstance(value, (bytes, bytearray)):
        data = bytes(value)
        filename = None
    elif isinstance(value, str):
        data, filename = value.encode("utf-8"), None
    elif isinstance(value, dict):
        filename = value.get("filename")
        if "bytes" in value and isinstance(value["bytes"], (bytes, bytearray)):
            data = bytes(value["bytes"])
        elif "base64" in value:
            try:
                data = base64.b64decode(value["base64"])
            except Exception as exc:
                raise OpenApiHttpExecutionError(f"Invalid base64 file input: {exc}") from exc
        elif "text" in value:
            data = str(value["text"]).encode("utf-8")
        elif "pod_path" in value:
            raise OpenApiHttpExecutionError(
                "File input references a pod path but no pod context was available "
                "to resolve it; pass {\"base64\": ...} or run within a pod."
            )
        else:
            raise OpenApiHttpExecutionError(
                "File input object must contain 'pod_path', 'base64', or 'text'."
            )
    else:
        raise OpenApiHttpExecutionError(f"Unsupported file input type: {type(value)!r}")

    if len(data) > _MAX_FILE_BYTES:
        raise OpenApiHttpExecutionError(
            f"File input exceeds the {_MAX_FILE_BYTES // (1024 * 1024)}MB limit."
        )
    return data, filename


def _summarize_error_body(content: bytes | None, *, limit: int = 600) -> str:
    if not content:
        return ""
    try:
        text = content.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""
    return text[:limit]


class OpenApiHttpExecutor:
    """Executes a connector operation described by an ``execution`` descriptor."""

    async def execute(
        self,
        *,
        connector_id: str,
        operation_name: str,
        execution: dict[str, Any],
        payload: dict[str, Any],
        third_party_credentials: dict[str, Any] | None,
        connection_config: dict[str, Any] | None = None,
    ) -> Any:
        connection_config = connection_config or {}
        mode = (execution or {}).get("mode", "openapi")
        base_url = self._resolve_base_url(execution, third_party_credentials, connection_config)
        auth_headers, auth_query = self._resolve_auth(third_party_credentials)
        default_headers = self._default_headers(execution, connection_config)

        if mode == "raw":
            method, url, params, headers, req = self._build_raw(
                execution, payload, base_url, auth_headers, auth_query, default_headers
            )
            follow_redirects = False
        else:
            method, url, params, headers, req = self._build_openapi(
                execution, payload, base_url, auth_headers, auth_query, default_headers
            )
            follow_redirects = True

        want_binary = bool((execution.get("response") or {}).get("binary"))
        logger.debug(
            "connectors.openapi_http_executor.calling_http_operation.observed",
            connector_id=connector_id,
            operation_name=operation_name,
            http_method=method,
            mode=mode,
        )
        async with httpx.AsyncClient(
            follow_redirects=follow_redirects, timeout=_DEFAULT_TIMEOUT_SECONDS
        ) as client:
            response = await client.request(method, url, params=params, headers=headers, **req)

        return self._handle_response(response, operation_name, want_binary=want_binary)

    # --- request building ---------------------------------------------------

    def _resolve_base_url(
        self,
        execution: dict[str, Any],
        creds: dict[str, Any] | None,
        connection_config: dict[str, Any] | None = None,
    ) -> str:
        # Precedence: per-account base_url (e.g. Jira cloud) > per-instance
        # connection config server_url (generic OpenAPI-URL connector) > descriptor.
        creds = creds or {}
        connection_config = connection_config or {}
        user_data = creds.get("user_data") or {}
        account_base = creds.get("base_url") or user_data.get("base_url")
        base = account_base or connection_config.get("server_url") or execution.get("server_url")
        if not base:
            raise OpenApiHttpExecutionError("No base URL configured for the operation.")
        return base.rstrip("/")

    def _resolve_auth(
        self, creds: dict[str, Any] | None
    ) -> tuple[dict[str, str], dict[str, str]]:
        # Build auth directly from the credentials dict (no dependency on the
        # vendored typed-credential classes / isinstance checks).
        creds = creds or {}
        access_token = creds.get("access_token")
        if access_token:
            token_type = creds.get("token_type") or "Bearer"
            return {"Authorization": f"{token_type} {access_token}"}, {}
        api_key = creds.get("api_key")
        if api_key:
            return {"Authorization": f"Bearer {api_key}"}, {}
        return {}, {}

    def _default_headers(
        self, execution: dict[str, Any], connection_config: dict[str, Any] | None = None
    ) -> dict[str, str]:
        headers = dict(execution.get("default_headers") or {})
        # Per-instance connection config headers override the descriptor's.
        headers.update((connection_config or {}).get("default_headers") or {})
        headers.setdefault("User-Agent", _DEFAULT_USER_AGENT)
        return headers

    def _build_openapi(
        self,
        execution: dict[str, Any],
        payload: dict[str, Any],
        base_url: str,
        auth_headers: dict[str, str],
        auth_query: dict[str, str],
        default_headers: dict[str, str],
    ):
        payload = payload or {}
        path = execution["path"]
        for name in execution.get("path_params", []):
            if name not in payload or payload[name] is None:
                raise OpenApiHttpExecutionError(f"Missing required path parameter '{name}'.")
            path = path.replace("{" + name + "}", quote(_scalar(payload[name]), safe=""))

        params: list[tuple[str, str]] = list(auth_query.items())
        for spec in execution.get("query_params", []):
            name = spec["name"]
            if name not in payload or payload[name] is None:
                continue
            params.extend(self._encode_query(name, payload[name], spec))

        headers = dict(default_headers)
        for name in execution.get("header_params", []):
            if name in payload and payload[name] is not None:
                headers[name] = _scalar(payload[name])
        headers.update(auth_headers)

        req = self._build_body(execution.get("request_body"), payload, headers)
        return execution["method"], f"{base_url}{path}", params, headers, req

    def _build_raw(
        self,
        execution: dict[str, Any],
        payload: dict[str, Any],
        base_url: str,
        auth_headers: dict[str, str],
        auth_query: dict[str, str],
        default_headers: dict[str, str],
    ):
        payload = payload or {}
        method = str(payload.get("method") or "").upper()
        if not method:
            raise OpenApiHttpExecutionError("Raw request requires a 'method'.")
        path = str(payload.get("path") or "")
        # SSRF guard: only relative paths on the connector's own host are allowed.
        if not path.startswith("/") or path.startswith("//"):
            raise OpenApiHttpExecutionError(
                "Raw request 'path' must be an absolute path (e.g. /repos/o/r) on the connector host."
            )
        url = f"{base_url}{path}"
        if urlsplit(url).netloc != urlsplit(base_url).netloc:
            raise OpenApiHttpExecutionError("Raw request path may not target another host.")

        params: list[tuple[str, str]] = list(auth_query.items())
        for name, value in (payload.get("query") or {}).items():
            if value is None:
                continue
            if isinstance(value, list):
                params.extend((name, _scalar(v)) for v in value)
            else:
                params.append((name, _scalar(value)))

        headers = dict(default_headers)
        for name, value in (payload.get("headers") or {}).items():
            headers[name] = _scalar(value)
        headers.update(auth_headers)

        req: dict[str, Any] = {}
        body = payload.get("body")
        if body is not None:
            if isinstance(body, dict) and ({"base64", "pod_path", "text", "bytes"} & set(body)):
                data, _ = _coerce_file_bytes(body)
                req["content"] = data
            else:
                req["json"] = body
        return method, url, params, headers, req

    def _encode_query(self, name: str, value: Any, spec: dict[str, Any]) -> list[tuple[str, str]]:
        if isinstance(value, list):
            explode = spec.get("explode")
            style = spec.get("style") or "form"
            if style == "form" and explode is False:
                return [(name, ",".join(_scalar(v) for v in value))]
            # default form/explode=true → repeat the key
            return [(name, _scalar(v)) for v in value]
        return [(name, _scalar(value))]

    def _build_body(
        self,
        request_body: dict[str, Any] | None,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        if not request_body:
            return {}
        body = payload.get(request_body.get("field", "body"))
        if body is None:
            return {}
        content_type = (request_body.get("content_type") or "application/json").split(";", 1)[0].strip().lower()
        binary_fields = request_body.get("binary_fields") or []

        if content_type == "application/json" and not binary_fields:
            return {"json": body}

        if content_type == "multipart/form-data":
            files = {}
            data = {}
            body = body if isinstance(body, dict) else {}
            for name in binary_fields:
                if name in body and body[name] is not None:
                    raw, filename = _coerce_file_bytes(body[name])
                    files[name] = (filename or name, raw)
            for name in request_body.get("form_fields") or []:
                if name in body and body[name] is not None:
                    data[name] = _scalar(body[name])
            return {"files": files, "data": data}

        # Single-blob body (octet-stream / */*): send raw bytes.
        raw, _ = _coerce_file_bytes(body)
        headers.setdefault("Content-Type", content_type or "application/octet-stream")
        return {"content": raw}

    # --- response handling --------------------------------------------------

    def _handle_response(self, response: httpx.Response, operation_name: str, *, want_binary: bool):
        status = response.status_code
        if status >= 400:
            body = _summarize_error_body(response.content)
            raise OpenApiHttpExecutionError(
                f"{operation_name} failed: HTTP {status}." + (f" {body}" if body else ""),
                status_code=status,
                details={"error": body, "status_code": status},
            )
        if status == 204 or not response.content:
            return {}

        content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        is_json = content_type == "application/json" or content_type.endswith("+json")
        if want_binary or not is_json:
            return BinaryContentResult.from_http_response(
                response, fallback_media_type=content_type or "application/octet-stream"
            )
        try:
            return response.json()
        except Exception:
            # Content typed as JSON but not parseable — return as binary rather than crash.
            return BinaryContentResult.from_http_response(
                response, fallback_media_type=content_type or "application/octet-stream"
            )
