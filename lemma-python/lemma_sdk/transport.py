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
# gateway errors where the request may never have reached the handler. 500 is
# excluded (it may indicate a partial side effect).
_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})

# 429 is refused by the rate limiter before the handler runs, so replaying it
# cannot repeat a side effect whatever the method. A gateway error carries no
# such promise -- a 504 usually means the handler is still running -- so those
# are retried only for methods that can be replayed safely.
_ALWAYS_RETRYABLE_STATUS = frozenset({429})

# GET/HEAD/OPTIONS only. PUT and DELETE are idempotent by HTTP semantics, but
# replaying one that in fact succeeded turns a success into a 404 or a lost
# update, which reads as a worse failure than the gateway error it replaced.
_REPLAYABLE_METHODS = frozenset({"get", "head", "options"})


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
        self._timeout = timeout
        self._max_retries = max(0, max_retries)

    @property
    def timeout(self) -> float:
        """The configured per-request timeout, in seconds."""
        return self._timeout

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
            # Short-circuit order matters: reading the verb rebuilds the
            # request, so it only happens on the rare path where a retry is
            # otherwise on the table.
            if (
                status_code in _RETRYABLE_STATUS
                and attempt < self._max_retries
                and _should_retry(
                    status_code, _endpoint_method(endpoint, *path_args, **kwargs)
                )
            ):
                time.sleep(_retry_delay(attempt, headers.get("retry-after")))
                attempt += 1
                continue
            if status_code >= 400:
                raise self.error_from_response(
                    status_code, response.parsed, response.content, headers
                )
            return response.parsed

    def stream(
        self,
        endpoint: Any,
        *path_args: Any,
        read_timeout: float | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Open a generated endpoint as a stream, without buffering the body.

        Returns the live ``httpx.Response`` for the caller to iterate and close;
        an error status is drained and raised as the same typed error every
        other call raises.

        ``read_timeout`` is the deadline between two reads, and defaults to none
        because the caller is usually reading Server-Sent Events. A download,
        whose bytes arrive continuously, should pass ``transport.timeout`` so a
        dead connection is not waited on forever.

        Never retried. A streaming endpoint here either starts an agent or
        workflow run -- where a replay would start a second one -- or is a
        download whose partial body the caller already holds.
        """
        request_kwargs = endpoint._get_kwargs(*path_args, **kwargs)
        client = self.generated.get_httpx_client()
        request = client.build_request(
            **request_kwargs, timeout=self._stream_timeout(read_timeout)
        )
        try:
            response = client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            raise LemmaTimeoutError(str(exc) or "Request timed out") from exc
        except httpx.TransportError as exc:
            raise LemmaConnectionError(str(exc) or "Network request failed") from exc

        if response.status_code >= 400:
            content = response.read()
            response.close()
            raise self.error_from_response(
                response.status_code, None, content, response.headers
            )
        return response

    def _stream_timeout(self, read_timeout: float | None) -> httpx.Timeout:
        """Connect/write/pool keep the configured timeout; read is the caller's.

        An SSE stream is paced by the server -- the gap between two events is an
        agent thinking, not a stalled socket -- so a read deadline would cut
        every run longer than it short, which is what the buffered path did.
        """
        return httpx.Timeout(self._timeout, read=read_timeout)

    def error_from_response(
        self,
        status_code: int,
        parsed: Any | None,
        content: bytes | bytearray | str | None,
        headers: Any | None = None,
    ) -> LemmaAPIError:
        """Map a failed response onto the SDK's typed error hierarchy.

        Public so the few facades that call httpx directly raise the same
        subclasses (and carry the same `code` / `details` / `X-Request-Id`) as
        every typed call, instead of a bare LemmaAPIError no `except
        LemmaNotFoundError` can catch.
        """
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

        Retries and error mapping match the typed resources -- which means a
        gateway error is only replayed for a method that can be replayed
        safely. Returns parsed JSON when the response is JSON, otherwise the
        response text.
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
            if _should_retry(status_code, method) and attempt < self._max_retries:
                time.sleep(_retry_delay(attempt, response.headers.get("retry-after")))
                attempt += 1
                continue
            if status_code >= 400:
                raise self.error_from_response(
                    status_code, None, response.content, response.headers
                )
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                return response.json()
            return response.text


def _endpoint_method(endpoint: Any, *path_args: Any, **kwargs: Any) -> str | None:
    """The HTTP verb a generated endpoint issues, or None when it cannot be read.

    Every generated module builds its request through ``_get_kwargs``, whose
    result carries the verb; the retry loop calls it once per attempt anyway, so
    asking it again on the attempt that failed costs no more than the attempt
    did. ``None`` means "assume not replayable", which only ever costs a retry.
    """
    build = getattr(endpoint, "_get_kwargs", None)
    if build is None:
        return None
    method = build(*path_args, **kwargs).get("method")
    return method if isinstance(method, str) else None


def _should_retry(status_code: int, method: str | None) -> bool:
    """Whether a failed request may be sent again.

    Retrying a write the server is still processing is how one
    ``records.create`` becomes two rows, so a gateway error is replayed only for
    a method that carries no side effect.
    """
    if status_code not in _RETRYABLE_STATUS:
        return False
    if status_code in _ALWAYS_RETRYABLE_STATUS:
        return True
    return method is not None and method.lower() in _REPLAYABLE_METHODS


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
