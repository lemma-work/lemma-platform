from __future__ import annotations

import email.utils
import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .errors import (
    LemmaAPIError,
    LemmaConnectionError,
    LemmaTimeoutError,
    api_error,
)
from .openapi_client import AuthenticatedClient

MISSING = object()

# Conservative retry set: 429 is an explicit back-off, and 502/503/504 are
# gateway errors where the request usually never reached the handler. 500 is
# excluded (it may indicate a partial side effect).
_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})


# Callers that are something more specific than "a program using the SDK" say
# so through this variable, because the backend cannot tell them apart from the
# request alone. The CLI sets it; anything unrecognised is ignored server-side
# and read as a plain SDK call, which is the honest default -- an unknown caller
# must never be counted as a person in a browser.
_CLIENT_ENV_VAR = "LEMMA_CLIENT"
_KNOWN_CLIENTS = frozenset({"lemma-cli", "lemma-desktop", "lemma-web", "lemma-app"})


def _client_header() -> str:
    """Identify this SDK + version on every request so the backend can log which
    client hit an endpoint (read drift-free from installed package metadata)."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            ver = version("lemma-sdk")
        except PackageNotFoundError:
            ver = "unknown"
    except Exception:  # pragma: no cover - importlib always present on Python 3.14
        ver = "unknown"
    import os

    declared = (os.environ.get(_CLIENT_ENV_VAR) or "").strip()
    if declared in _KNOWN_CLIENTS:
        return f"{declared}/{ver}"
    return f"lemma-sdk-py/{ver}"


class LemmaTransport:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout: float = 30.0,
        verify_ssl: bool = True,
        max_retries: int = 2,
    ) -> None:
        self.generated = AuthenticatedClient(
            base_url=base_url.rstrip("/"),
            token=token,
            timeout=timeout,
            verify_ssl=verify_ssl,
            headers={"X-Lemma-Client": _client_header()},
        )
        self._max_retries = max(0, max_retries)

    def close(self) -> None:
        if getattr(self.generated, "_client", None) is not None:
            self.generated.get_httpx_client().close()

    def call(
        self,
        endpoint: Any,
        *path_args: Any,
        body: Any = MISSING,
        body_model: Any = None,
        **kwargs: Any,
    ) -> Any:
        if body is not MISSING:
            kwargs["body"] = (
                body_model.from_dict(body)
                if body_model and isinstance(body, dict)
                else body
            )

        attempt = 0
        while True:
            try:
                response = endpoint.sync_detailed(
                    *path_args, client=self.generated, **kwargs
                )
            except httpx.TimeoutException as exc:
                raise LemmaTimeoutError(str(exc) or "Request timed out") from exc
            except httpx.TransportError as exc:
                raise LemmaConnectionError(
                    str(exc) or "Network request failed"
                ) from exc

            status_code = int(response.status_code)
            headers = getattr(response, "headers", {}) or {}
            if status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                time.sleep(_retry_delay(attempt, headers.get("retry-after")))
                attempt += 1
                continue
            if status_code >= 400:
                raise self._error_from_response(
                    status_code, response.parsed, response.content, headers
                )
            return response.parsed

    def _error_from_response(
        self,
        status_code: int,
        parsed: Any | None,
        content: bytes | bytearray | str | None,
        headers: Any | None = None,
    ) -> LemmaAPIError:
        payload = _to_plain(parsed) if parsed is not None else _parse_content(content)
        message = "Request failed"
        code = None
        details = None
        if isinstance(payload, dict):
            code = payload.get("code")
            details = payload.get("details")
            detail = payload.get("detail")
            if details is None and isinstance(detail, list):
                # FastAPI validation error (422): keep the structured field list in
                # `details` (so clients can render `field: msg`) and use a clean
                # summary message instead of dumping the raw list into the message.
                details = detail
                message = str(payload.get("message") or "Validation error")
            else:
                message = str(payload.get("message") or detail or message)
        elif payload is not None:
            message = str(payload)
        retry_after = None
        request_id = None
        if headers:
            request_id = headers.get("x-request-id")
            if status_code == 429:
                retry_after = _retry_after_seconds(headers.get("retry-after"))
        return api_error(
            status_code,
            message,
            code=code,
            details=details,
            raw_response=payload,
            retry_after=retry_after,
            request_id=request_id,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = MISSING,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Raw authenticated request escape hatch (base_url + auth applied).

        Retries and error mapping match the typed resources. Returns parsed JSON
        when the response is JSON, otherwise the response text.
        """
        client = self.generated.get_httpx_client()
        kwargs: dict[str, Any] = {}
        if params is not None:
            kwargs["params"] = params
        if json_body is not MISSING:
            kwargs["json"] = json_body
        if headers:
            kwargs["headers"] = headers

        attempt = 0
        while True:
            try:
                response = client.request(method, path, **kwargs)
            except httpx.TimeoutException as exc:
                raise LemmaTimeoutError(str(exc) or "Request timed out") from exc
            except httpx.TransportError as exc:
                raise LemmaConnectionError(
                    str(exc) or "Network request failed"
                ) from exc

            status_code = response.status_code
            if status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                time.sleep(_retry_delay(attempt, response.headers.get("retry-after")))
                attempt += 1
                continue
            if status_code >= 400:
                raise self._error_from_response(
                    status_code, None, response.content, response.headers
                )
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                return response.json()
            return response.text


def _retry_after_seconds(value: Any) -> float | None:
    """Parse a Retry-After header (delta-seconds or HTTP-date) into seconds."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except TypeError, ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(str(value))
    except TypeError, ValueError:
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())


def _retry_delay(attempt: int, retry_after: Any = None) -> float:
    """Backoff for retry ``attempt`` (0-based), honoring Retry-After when given."""
    seconds = _retry_after_seconds(retry_after)
    if seconds is not None:
        return min(seconds, 30.0)
    return min(0.5 * (2**attempt), 6.0)


def _to_plain(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_plain(item) for key, item in value.items()}
    if hasattr(value, "to_dict"):
        return _to_plain(value.to_dict())
    return value


def _parse_content(content: bytes | bytearray | str | None) -> Any | None:
    if content is None:
        return None
    raw = (
        content.decode("utf-8", errors="replace")
        if isinstance(content, (bytes, bytearray))
        else str(content)
    )
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw
