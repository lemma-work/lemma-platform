from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from lemma_sdk.auth import refresh_cli_session


@pytest.mark.parametrize("payload", [[], "not-an-object", 42, None])
def test_refresh_error_handles_non_object_json_payloads(
    monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    response = SimpleNamespace(
        status_code=401,
        json=lambda: payload,
        text="",
        reason="Unauthorized",
    )
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: response)

    with pytest.raises(ValueError, match="Unable to refresh session"):
        refresh_cli_session(
            base_url="https://api.example.test",
            refresh_token="refresh-token",
            verify_ssl=True,
            timeout=5,
        )


def test_refresh_error_uses_string_detail_when_message_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(
        status_code=401,
        json=lambda: {"detail": "Refresh token expired"},
        text="",
        reason="Unauthorized",
    )
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: response)

    with pytest.raises(ValueError, match="Refresh token expired"):
        refresh_cli_session(
            base_url="https://api.example.test",
            refresh_token="refresh-token",
            verify_ssl=True,
            timeout=5,
        )


def test_refresh_error_does_not_expose_non_string_message_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(
        status_code=401,
        json=lambda: {"message": {"internal": "details"}},
        text="",
        reason="Unauthorized",
    )
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: response)

    with pytest.raises(ValueError, match="Unable to refresh session"):
        refresh_cli_session(
            base_url="https://api.example.test",
            refresh_token="refresh-token",
            verify_ssl=True,
            timeout=5,
        )
