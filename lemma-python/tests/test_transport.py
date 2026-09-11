"""Transport-level tests: drive LemmaTransport.call() / .request() through their
retry + error-mapping + timeout paths against fakes (no live backend, no sleeps)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from lemma_sdk.errors import (
    LemmaAuthError,
    LemmaConnectionError,
    LemmaNotFoundError,
    LemmaRateLimitError,
    LemmaServerError,
    LemmaTimeoutError,
)
from lemma_sdk.transport import _RETRYABLE_STATUS, LemmaTransport


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    # Make retry backoff instant so tests don't actually wait.
    monkeypatch.setattr("time.sleep", lambda *_: None)


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        *,
        parsed: Any = None,
        content: bytes = b"",
        headers: dict | None = None,
    ):
        self.status_code = status_code
        self.parsed = parsed
        self.content = content
        self.headers = headers or {}


class FakeEndpoint:
    """Stands in for a generated endpoint module.

    Implements the two entry points the transport uses: ``_get_kwargs``, which
    every generated module builds its request through (and which the transport
    reads the HTTP verb from), and ``sync_detailed``.
    """

    __name__ = "fake_endpoint"

    def __init__(self, outcomes: list[Any], *, method: str = "get"):
        self._outcomes = list(outcomes)
        self._method = method
        self.calls = 0

    def _get_kwargs(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"method": self._method, "url": "/fake"}

    def sync_detailed(self, *args: Any, client: Any = None, **kwargs: Any) -> Any:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_transport(max_retries: int = 2) -> LemmaTransport:
    return LemmaTransport(
        base_url="https://api.example.test", token="t", max_retries=max_retries
    )


# --- .call() (typed-resource path) ----------------------------------------


def test_call_retries_retryable_status_then_succeeds():
    transport = make_transport()
    endpoint = FakeEndpoint([FakeResponse(503), FakeResponse(200, parsed={"ok": True})])
    assert transport.call(endpoint) == {"ok": True}
    assert endpoint.calls == 2


def test_call_honors_retry_after_then_succeeds():
    transport = make_transport()
    endpoint = FakeEndpoint(
        [
            FakeResponse(429, headers={"retry-after": "0"}),
            FakeResponse(200, parsed={"ok": True}),
        ]
    )
    assert transport.call(endpoint) == {"ok": True}
    assert endpoint.calls == 2


def test_call_does_not_replay_a_write_on_a_gateway_error():
    # A 504 usually means the handler is still running, so replaying the POST
    # would create a second record / start a second agent run.
    transport = make_transport()
    endpoint = FakeEndpoint(
        [FakeResponse(504), FakeResponse(200, parsed={"ok": True})], method="post"
    )
    with pytest.raises(LemmaServerError):
        transport.call(endpoint)
    assert endpoint.calls == 1


def test_call_still_retries_a_write_on_429():
    # The rate limiter refuses the request before the handler runs, so a replay
    # cannot repeat a side effect.
    transport = make_transport()
    endpoint = FakeEndpoint(
        [
            FakeResponse(429, headers={"retry-after": "0"}),
            FakeResponse(200, parsed={"ok": True}),
        ],
        method="post",
    )
    assert transport.call(endpoint) == {"ok": True}
    assert endpoint.calls == 2


def test_call_does_not_replay_when_the_method_is_unknown():
    # An endpoint the transport cannot read a verb from is assumed to write.
    class VerblessEndpoint(FakeEndpoint):
        _get_kwargs = None

    transport = make_transport()
    endpoint = VerblessEndpoint(
        [FakeResponse(503), FakeResponse(200, parsed={"ok": True})]
    )
    with pytest.raises(LemmaServerError):
        transport.call(endpoint)
    assert endpoint.calls == 1


def test_call_does_not_retry_500():
    transport = make_transport()
    endpoint = FakeEndpoint([FakeResponse(500, parsed={"message": "boom"})])
    with pytest.raises(LemmaServerError):
        transport.call(endpoint)
    assert endpoint.calls == 1  # 500 is excluded from the retry set


def test_call_maps_404_to_typed_error_with_request_id():
    transport = make_transport(max_retries=0)
    endpoint = FakeEndpoint(
        [
            FakeResponse(
                404,
                parsed={"message": "missing", "code": "not_found"},
                headers={"x-request-id": "req-1"},
            )
        ]
    )
    with pytest.raises(LemmaNotFoundError) as excinfo:
        transport.call(endpoint)
    err = excinfo.value
    assert err.code == "not_found"
    assert err.request_id == "req-1"
    assert err.message == "missing"


def test_call_exhausts_retries_and_surfaces_retry_after():
    transport = make_transport(max_retries=1)
    endpoint = FakeEndpoint(
        [
            FakeResponse(429, headers={"retry-after": "3"}),
            FakeResponse(429, headers={"retry-after": "3"}),
        ]
    )
    with pytest.raises(LemmaRateLimitError) as excinfo:
        transport.call(endpoint)
    assert excinfo.value.retry_after == 3.0
    assert endpoint.calls == 2  # initial + 1 retry


def test_call_maps_timeout_and_transport_errors():
    transport = make_transport(max_retries=0)
    with pytest.raises(LemmaTimeoutError):
        transport.call(FakeEndpoint([httpx.TimeoutException("slow")]))
    with pytest.raises(LemmaConnectionError):
        transport.call(FakeEndpoint([httpx.ConnectError("refused")]))


# --- .request() (raw escape hatch) ----------------------------------------


class FakeHttpxResponse:
    def __init__(
        self,
        status_code: int,
        *,
        json_body: Any = None,
        text: str = "",
        headers: dict | None = None,
    ):
        self.status_code = status_code
        self._json = json_body
        self.text = text
        self.content = (text or "").encode()
        self.headers = headers or {}
        if json_body is not None:
            self.headers.setdefault("content-type", "application/json")

    def json(self) -> Any:
        return self._json


class FakeHttpxClient:
    def __init__(self, outcomes: list[Any]):
        self._outcomes = list(outcomes)
        self.calls = 0

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _patch_httpx(
    transport: LemmaTransport, client: FakeHttpxClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # .request() only reaches generated.get_httpx_client(); swap the whole
    # generated client for a stub exposing it (the real method is read-only).
    monkeypatch.setattr(
        transport, "generated", SimpleNamespace(get_httpx_client=lambda: client)
    )


def test_request_returns_parsed_json(monkeypatch: pytest.MonkeyPatch):
    transport = make_transport()
    client = FakeHttpxClient([FakeHttpxResponse(200, json_body={"ok": True})])
    _patch_httpx(transport, client, monkeypatch)
    assert transport.request("GET", "/x") == {"ok": True}


def test_request_retries_a_read_then_succeeds(monkeypatch: pytest.MonkeyPatch):
    transport = make_transport()
    client = FakeHttpxClient(
        [FakeHttpxResponse(503), FakeHttpxResponse(200, json_body={"ok": True})]
    )
    _patch_httpx(transport, client, monkeypatch)
    assert transport.request("GET", "/x") == {"ok": True}
    assert client.calls == 2


def test_request_does_not_replay_a_write_on_a_gateway_error(
    monkeypatch: pytest.MonkeyPatch,
):
    transport = make_transport()
    client = FakeHttpxClient(
        [FakeHttpxResponse(503), FakeHttpxResponse(200, json_body={"ok": True})]
    )
    _patch_httpx(transport, client, monkeypatch)
    with pytest.raises(LemmaServerError):
        transport.request("POST", "/x")
    assert client.calls == 1


def test_request_maps_error_status(monkeypatch: pytest.MonkeyPatch):
    transport = make_transport(max_retries=0)
    client = FakeHttpxClient(
        [FakeHttpxResponse(404, text='{"message":"nope","code":"not_found"}')]
    )
    _patch_httpx(transport, client, monkeypatch)
    with pytest.raises(LemmaNotFoundError) as excinfo:
        transport.request("GET", "/x")
    assert excinfo.value.code == "not_found"


# --- .stream() (Server-Sent Events path) ----------------------------------


class StreamEndpoint:
    """A generated streaming endpoint: only `_get_kwargs` is ever used."""

    __name__ = "stream_endpoint"

    def _get_kwargs(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"method": "post", "url": "/stream"}


def stream_transport(
    handler: Any, *, max_retries: int = 2
) -> tuple[LemmaTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = make_transport(max_retries=max_retries)
    transport.generated.set_httpx_client(
        httpx.Client(
            transport=httpx.MockTransport(record),
            base_url="https://api.example.test",
        )
    )
    return transport, seen


def test_stream_sends_no_read_deadline():
    # The gap between two SSE frames is the agent thinking. A read deadline
    # would end every run longer than it -- the buffered path's 30s bug.
    transport, seen = stream_transport(
        lambda _: httpx.Response(200, content=b"data: {}\n\n")
    )
    response = transport.stream(StreamEndpoint())
    try:
        assert response.read() == b"data: {}\n\n"
    finally:
        response.close()
    timeout = seen[0].extensions["timeout"]
    assert timeout["read"] is None
    assert timeout["connect"] == 30.0


def test_stream_does_not_replay_a_run_on_a_gateway_error():
    transport, seen = stream_transport(lambda _: httpx.Response(503))
    with pytest.raises(LemmaServerError):
        transport.stream(StreamEndpoint())
    assert len(seen) == 1


def test_stream_maps_an_error_status_to_a_typed_error_with_request_id():
    transport, _ = stream_transport(
        lambda _: httpx.Response(
            404,
            json={"message": "no such conversation", "code": "not_found"},
            headers={"x-request-id": "req-7"},
        )
    )
    with pytest.raises(LemmaNotFoundError) as excinfo:
        transport.stream(StreamEndpoint())
    assert excinfo.value.code == "not_found"
    assert excinfo.value.request_id == "req-7"
    assert excinfo.value.message == "no such conversation"


# --- 401: refresh, then one replay ----------------------------------------


class _RefreshServer:
    """A refresh endpoint, and a record of what was asked of it."""

    def __init__(self, *, status: int = 200, payload: Any = None) -> None:
        self.status = status
        self.payload = (
            payload
            if payload is not None
            else {
                "access_token": "fresh",
                "refresh_token": "rotated",
            }
        )
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            {
                "url": str(request.url),
                "body": json.loads(request.content or b"{}"),
                "authorization": request.headers.get("authorization"),
            }
        )
        return httpx.Response(self.status, json=self.payload)


def _transport_with_refresh(server: _RefreshServer | None, **kwargs: Any):
    """A transport whose httpx client answers the refresh endpoint locally."""
    transport = LemmaTransport(
        base_url="https://api.example.test",
        token="stale",
        max_retries=2,
        **kwargs,
    )
    client = transport.generated.get_httpx_client()
    if server is not None:
        client._transport = httpx.MockTransport(server)
    return transport


def test_a_401_refreshes_once_and_replays_the_request():
    """The whole point: an access token that died mid-process is replaced.

    `LEMMA_REFRESH_TOKEN` was documented and did nothing, so a long-lived
    process using the SDK simply started failing when its token aged out.
    """
    server = _RefreshServer()
    transport = _transport_with_refresh(server, refresh_token="rt")
    endpoint = FakeEndpoint([FakeResponse(401), FakeResponse(200, parsed={"ok": True})])

    assert transport.call(endpoint) == {"ok": True}
    assert endpoint.calls == 2
    assert len(server.requests) == 1
    assert server.requests[0]["body"] == {"refresh_token": "rt"}
    assert server.requests[0]["url"].endswith("/auth/cli/refresh")


def test_the_replay_carries_the_new_token_on_the_client_already_built():
    """The header is baked into the `httpx.Client` and cached.

    Setting `generated.token` alone changes what a *future* client would send,
    so the replay would go out with the dead token and 401 again.
    """
    server = _RefreshServer()
    transport = _transport_with_refresh(server, refresh_token="rt")
    client = transport.generated.get_httpx_client()

    transport.call(FakeEndpoint([FakeResponse(401), FakeResponse(200, parsed={})]))

    assert transport.generated.token == "fresh"
    assert client.headers["Authorization"] == "Bearer fresh"


def test_a_401_never_loops():
    """One refresh per call, whatever the server keeps saying.

    The CLI shipped this exact spin, and every turn of it is a round trip
    against an endpoint that is already refusing.
    """
    server = _RefreshServer()
    transport = _transport_with_refresh(server, refresh_token="rt")
    endpoint = FakeEndpoint([FakeResponse(401)] * 6)

    with pytest.raises(LemmaAuthError):
        transport.call(endpoint)
    assert endpoint.calls == 2
    assert len(server.requests) == 1


def test_no_refresh_token_means_the_401_is_simply_raised():
    """Most callers have none; they must not pay a round trip to learn that."""
    server = _RefreshServer()
    transport = _transport_with_refresh(server)
    endpoint = FakeEndpoint([FakeResponse(401)])

    with pytest.raises(LemmaAuthError):
        transport.call(endpoint)
    assert endpoint.calls == 1
    assert server.requests == []


def test_a_refusing_refresh_endpoint_surfaces_the_original_401():
    """ "Your session expired", not whatever went wrong while renewing it."""
    server = _RefreshServer(status=401, payload={"message": "refresh token expired"})
    transport = _transport_with_refresh(server, refresh_token="rt")
    endpoint = FakeEndpoint([FakeResponse(401), FakeResponse(200, parsed={})])

    with pytest.raises(LemmaAuthError):
        transport.call(endpoint)
    assert endpoint.calls == 1


def test_an_error_status_is_not_a_session_whatever_its_body_says():
    """The status is checked, not just the shape of the body.

    An error envelope that happens to echo the fields it was sent -- a gateway,
    a proxy, a misconfigured error handler -- would otherwise read as a
    successful refresh, and the SDK would install a token the server never
    issued and replay the request with it.
    """
    server = _RefreshServer(
        status=403, payload={"access_token": "not-yours", "refresh_token": "nope"}
    )
    transport = _transport_with_refresh(server, refresh_token="rt")
    endpoint = FakeEndpoint([FakeResponse(401), FakeResponse(200, parsed={})])

    with pytest.raises(LemmaAuthError):
        transport.call(endpoint)
    assert endpoint.calls == 1
    assert transport.generated.token == "stale"


def test_401_is_not_in_the_retry_set():
    """Replaying a dead token just fails again; the refresh is the fix.

    Pinned separately because `_RETRYABLE_STATUS` is asserted by equality in
    `test_sdk_reliability.py`, and adding 401 there would make every expired
    session cost three identical rejections.
    """
    assert 401 not in _RETRYABLE_STATUS
