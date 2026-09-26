"""What a person reads when the model behind a teammate is not ready.

Each of these used to be a sentence about "the agent runtime configuration" or
a dropped connection. The failures are different -- a key the provider refuses,
a model server that is not running, a provider whose list could not be read --
and each has one place it is fixed.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from app.modules.agent.infrastructure.transport_errors import (
    local_model_server_down_message,
)
from app.modules.agent.services import runtime_provider_discovery as discovery
from app.modules.agent.services.run_finalizer import run_failure_message

pytestmark = pytest.mark.unit


def _refused(url: str) -> Exception:
    """What the OpenAI SDK raises when nothing listens: its own wrapper around
    httpx's `ConnectError`."""
    request = httpx.Request("POST", url)
    cause = httpx.ConnectError("All connection attempts failed", request=request)

    class APIConnectionError(Exception):
        pass

    wrapper = APIConnectionError("Connection error.")
    wrapper.__cause__ = cause
    return wrapper


class TestLocalModelServerDown:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("http://127.0.0.1:11434/v1/chat/completions", "Ollama"),
            ("http://localhost:1234/v1/chat/completions", "LM Studio"),
            ("http://127.0.0.1:8080/v1/chat/completions", "The model server"),
        ],
    )
    def test_a_refused_loopback_names_what_to_start(
        self, url: str, expected: str
    ) -> None:
        message = local_model_server_down_message(_refused(url))

        assert message == (
            f"{expected} isn't running on this computer. Start it, then send again."
        )

    def test_the_run_failure_says_so_instead_of_dropped_connection(self) -> None:
        message = run_failure_message(
            _refused("http://127.0.0.1:11434/v1/chat/completions")
        )

        assert message.startswith("Ollama isn't running on this computer.")
        assert "kept dropping" not in message

    def test_a_remote_provider_is_not_called_a_local_server(self) -> None:
        assert (
            local_model_server_down_message(
                _refused("https://api.provider.test/v1/chat/completions")
            )
            is None
        )


class TestDiscovery:
    @pytest.mark.parametrize(
        "base_url,expected",
        [
            ("https://api.anthropic.com", "https://api.anthropic.com/v1/models"),
            ("https://api.anthropic.com/", "https://api.anthropic.com/v1/models"),
            ("https://gateway.test/v1", "https://gateway.test/v1/models"),
        ],
    )
    def test_anthropic_lists_models_under_v1(
        self, base_url: str, expected: str
    ) -> None:
        assert discovery._anthropic_models_url(base_url) == expected

    def test_an_unreadable_list_names_the_provider_and_the_way_out(self) -> None:
        with pytest.raises(ValueError) as caught:
            discovery._provider_model_catalog(
                discovered_models=[],
                fallback_model_names=[],
                provider_name="Acme",
            )

        assert str(caught.value) == (
            "Couldn't read the model list from Acme. Check the key, or type a "
            "model name below."
        )


class _Status:
    code = 401


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
        self.send_response(_Status.code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"data": []}')

    def log_message(self, *_: object) -> None:
        return None


@pytest.fixture
def provider_answering() -> Iterator[str]:
    """A model route on this machine that answers with `_Status.code`."""
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()


class TestRejectedKey:
    def test_a_refused_key_is_reported_not_treated_as_an_empty_list(
        self, provider_answering: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_Status, "code", 401)

        with pytest.raises(discovery.ProviderKeyRejectedError):
            asyncio.run(
                discovery._discover_openai_compatible_models(
                    base_url=provider_answering, api_key="wrong", headers={}
                )
            )

        assert discovery.key_rejected_message("Acme") == ("Acme rejected this API key.")

    def test_any_other_failure_still_falls_back_to_typed_names(
        self, provider_answering: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_Status, "code", 500)

        found = asyncio.run(
            discovery._discover_openai_compatible_models(
                base_url=provider_answering, api_key="k", headers={}
            )
        )

        assert found == []


class TestSharedInstallation:
    def test_a_model_on_this_computer_is_refused_with_the_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """While Lemma is shared, a loopback route would hand other people's
        requests to this machine; "must be a public URL" read as a typo."""
        from app.core.config import settings
        from app.core.exposure import exposure_settings

        monkeypatch.setattr(settings, "environment", "local")
        monkeypatch.setattr(exposure_settings, "installation_shared", True)

        with pytest.raises(ValueError) as caught:
            asyncio.run(
                discovery._validate_public_base_url("http://127.0.0.1:11434/v1")
            )

        assert str(caught.value) == (
            "Models on this computer can't be added while Lemma is shared: "
            "other people's requests would reach your computer. Turn sharing "
            "off to add it."
        )
