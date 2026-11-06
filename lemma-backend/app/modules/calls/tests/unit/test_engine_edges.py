"""The edges a call's engines and settings must hold: a stream that always
ends, blank settings that mean unset, an interruption measured by what was
heard, a refused session that ends the call, and a socket path only a call's
shape opens."""

from __future__ import annotations

import asyncio
import time

import pytest
from pydantic import JsonValue

from app.core.auth_exemptions import is_self_authenticated_websocket
from app.modules.calls.config import CallsSettings
from app.modules.calls.domain.frames import offer
from app.modules.calls.domain.voice import EngineClosed, EngineUnavailable, Interrupted
from app.modules.calls.infrastructure.engines.openai_realtime import (
    OpenAIRealtimeEngine,
)
from app.modules.calls.infrastructure.engines.google_auth import (
    service_account_headers,
    vertex_endpoint,
    vertex_model,
)
from app.modules.calls.services.engines import engines_offered

pytestmark = pytest.mark.unit


async def test_the_event_that_ends_a_stream_is_never_the_one_dropped() -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=2)
    offer(queue, "audio 1")
    offer(queue, "audio 2")
    offer(queue, "audio 3")  # dropped: the reader is that far behind
    offer(queue, EngineClosed("gone"), essential=True)
    drained = [queue.get_nowait() for _ in range(queue.qsize())]
    assert drained == ["audio 2", EngineClosed("gone")]


def test_a_blank_setting_is_unset_and_an_absent_one_keeps_its_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("GEMINI_API_KEY", "OPENAI_API_KEY", "DEEPGRAM_API_KEY"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("CASCADE_EAGER_EOT_THRESHOLD", "")
    monkeypatch.delenv("CASCADE_TALKER_REASONING_EFFORT", raising=False)
    settings = CallsSettings(_env_file=None)
    assert settings.gemini_api_key is None
    assert settings.cascade_eager_eot_threshold is None  # wait for the confirmed end
    assert settings.cascade_talker_reasoning_effort == "none"
    assert engines_offered(settings) == []


class _Sent(OpenAIRealtimeEngine):
    def __init__(self) -> None:
        super().__init__(api_key="k", model="m", instructions="i", voice="v")
        self.sent: list[dict[str, JsonValue]] = []

    async def _send(self, frame: dict[str, JsonValue]) -> None:
        self.sent.append(frame)

    def drain(self) -> list[object]:
        return [self._events.get_nowait() for _ in range(self._events.qsize())]


async def test_a_reply_still_playing_after_the_provider_finished_is_cut_where_it_was_heard() -> (
    None
):
    engine = _Sent()
    await engine._handle({"type": "response.created"})
    # Two seconds of audio delivered at once, faster than it plays.
    audio = "AAAA" * 16_000  # 48,000 bytes of silence: one second at 24 kHz
    for _ in range(2):
        await engine._handle(
            {"type": "response.output_audio.delta", "item_id": "it-1", "delta": audio}
        )
    await engine._handle({"type": "response.done"})
    assert engine._audio_started_at is not None
    engine._audio_started_at = time.monotonic() - 0.5  # half a second has played
    await engine._handle({"type": "input_audio_buffer.speech_started"})
    assert Interrupted() in engine.drain()
    [truncate] = [f for f in engine.sent if f["type"] == "conversation.item.truncate"]
    assert truncate["item_id"] == "it-1"
    assert 450 <= int(truncate["audio_end_ms"]) <= 1000  # heard, not delivered


async def test_a_session_the_provider_refused_ends_the_call_and_a_later_error_does_not() -> (
    None
):
    engine = _Sent()
    refused = await engine._handle(
        {"type": "error", "error": {"code": "invalid_value", "message": "bad voice"}}
    )
    assert isinstance(refused, EngineClosed) and refused.error
    engine = _Sent()
    await engine._handle({"type": "session.updated"})
    later = await engine._handle(
        {"type": "error", "error": {"code": "conversation_already_has_active_response"}}
    )
    assert later is None


@pytest.mark.parametrize(
    ("path", "exempt"),
    [
        (
            "/pods/01a15fc5-0e7e-7479-92f2-6b0b42c0d946/calls/01a15fc5-57b0-7477-a199-2b84454e928f/socket",
            True,
        ),
        ("/pods/01a15fc5-0e7e-7479-92f2-6b0b42c0d946/calls/not-a-uuid/socket", False),
        (
            "/pods/01a15fc5-0e7e-7479-92f2-6b0b42c0d946/calls/01a15fc5-57b0-7477-a199-2b84454e928f",
            False,
        ),
        ("/pods/01a15fc5-0e7e-7479-92f2-6b0b42c0d946/calls", False),
        ("/pods/01a15fc5-0e7e-7479-92f2-6b0b42c0d946/calls/x/socket/extra", False),
    ],
)
def test_only_a_calls_socket_path_skips_the_http_gate(path: str, exempt: bool) -> None:
    assert is_self_authenticated_websocket(path) is exempt


def test_a_vertex_project_offers_gemini_live_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_VERTEX_PROJECT", "lemma-development")
    settings = CallsSettings(_env_file=None)
    assert "gemini_live" in engines_offered(settings)


def test_vertex_names_the_regional_endpoint_and_the_full_model_path() -> None:
    live = "/ws/google.cloud.aiplatform.v1.LlmBidiService/BidiGenerateContent"
    assert (
        vertex_endpoint("us-central1")
        == "wss://us-central1-aiplatform.googleapis.com" + live
    )
    assert vertex_endpoint("global") == "wss://aiplatform.googleapis.com" + live
    assert (
        vertex_model("p", "us-central1", "models/gemini-live")
        == "projects/p/locations/us-central1/publishers/google/models/gemini-live"
    )
    full = "projects/p/locations/global/publishers/google/models/x"
    assert vertex_model("other", "us-central1", full) == full


async def test_the_service_account_token_becomes_a_bearer_header() -> None:
    import httpx

    def answer(request: httpx.Request) -> httpx.Response:
        assert request.headers["Metadata-Flavor"] == "Google"
        return httpx.Response(200, json={"access_token": "t0k", "expires_in": 3599})

    headers = service_account_headers(transport=httpx.MockTransport(answer))
    assert await headers() == {"Authorization": "Bearer t0k"}


async def test_no_metadata_server_is_an_unavailable_voice_not_a_crash() -> None:
    import httpx

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no metadata server here")

    headers = service_account_headers(transport=httpx.MockTransport(refuse))
    with pytest.raises(EngineUnavailable):
        await headers()
