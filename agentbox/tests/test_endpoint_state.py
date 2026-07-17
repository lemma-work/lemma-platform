from __future__ import annotations

import base64
import json

import pytest
from cryptography.exceptions import InvalidTag

from agentbox.config import settings
from agentbox.endpoint_state import (
    open_endpoint_state,
    seal_endpoint_state,
    validate_endpoint_state_keyring,
)


def test_endpoint_keyring_is_required_and_validated_at_startup(monkeypatch) -> None:
    monkeypatch.setattr(settings, "agentbox_endpoint_state_keys", "")
    with pytest.raises(RuntimeError, match="must contain"):
        validate_endpoint_state_keyring()

    monkeypatch.setattr(settings, "agentbox_endpoint_state_keys", "not-base64!")
    with pytest.raises(ValueError, match="encoding"):
        validate_endpoint_state_keyring()


def test_endpoint_credentials_are_encrypted_and_generation_bound() -> None:
    endpoints = {
        "runtime": {
            "base_url": "https://8080-sandbox.e2b.app",
            "headers": {"e2b-traffic-access-token": "provider-secret"},
        }
    }

    sealed = seal_endpoint_state("sandbox", 7, endpoints)

    assert "provider-secret" not in json.dumps(sealed)
    assert open_endpoint_state("sandbox", 7, sealed) == endpoints
    with pytest.raises(InvalidTag):
        open_endpoint_state("sandbox", 8, sealed)


def test_endpoint_keyring_supports_rotation_and_rejects_downgrade(monkeypatch) -> None:
    old_key = base64.urlsafe_b64encode(b"o" * 32).decode()
    new_key = base64.urlsafe_b64encode(b"n" * 32).decode()
    monkeypatch.setattr(settings, "agentbox_endpoint_state_keys", old_key)
    sealed = seal_endpoint_state(
        "sandbox",
        1,
        {"runtime": {"base_url": "https://runtime.example"}},
    )

    monkeypatch.setattr(
        settings,
        "agentbox_endpoint_state_keys",
        f"{new_key},{old_key}",
    )
    assert open_endpoint_state("sandbox", 1, sealed)["runtime"]["base_url"] == (
        "https://runtime.example"
    )

    monkeypatch.setattr(settings, "agentbox_endpoint_state_keys", new_key)
    with pytest.raises(ValueError, match="key is unavailable"):
        open_endpoint_state("sandbox", 1, sealed)
    with pytest.raises(ValueError, match="Unsupported"):
        open_endpoint_state(
            "sandbox",
            1,
            {"runtime": {"base_url": "http://forged.internal"}},
        )
