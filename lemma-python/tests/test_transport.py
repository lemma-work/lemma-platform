"""Transport-level tests: drive LemmaTransport.call() / .request() through their
retry + error-mapping + timeout paths against fakes (no live backend, no sleeps)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from lemma_sdk.errors import (
    LemmaConnectionError,
    LemmaNotFoundError,
    LemmaRateLimitError,
    LemmaServerError,
    LemmaTimeoutError,
)
from lemma_sdk.transport import LemmaTransport


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
    """Stands in for a generated endpoint module with sync_detailed()."""

    __name__ = "fake_endpoint"

    def __init__(self, outcomes: list[Any]):
        self._outcomes = list(outcomes)
        self.calls = 0

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


def test_request_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch):
    transport = make_transport()
    client = FakeHttpxClient(
        [FakeHttpxResponse(503), FakeHttpxResponse(200, json_body={"ok": True})]
    )
    _patch_httpx(transport, client, monkeypatch)
    assert transport.request("POST", "/x") == {"ok": True}
    assert client.calls == 2


def test_request_maps_error_status(monkeypatch: pytest.MonkeyPatch):
    transport = make_transport(max_retries=0)
    client = FakeHttpxClient(
        [FakeHttpxResponse(404, text='{"message":"nope","code":"not_found"}')]
    )
    _patch_httpx(transport, client, monkeypatch)
    with pytest.raises(LemmaNotFoundError) as excinfo:
        transport.request("GET", "/x")
    assert excinfo.value.code == "not_found"
