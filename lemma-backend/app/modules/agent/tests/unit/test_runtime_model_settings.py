"""Provider extensions survive validation of shared model settings."""

import pytest
from pydantic import ValidationError

from app.modules.agent.services.runtime_model_factory import provider_model_settings


def test_provider_settings_preserve_extensions_and_validate_known_fields() -> None:
    settings = provider_model_settings(
        {
            "max_tokens": 256,
            "openai_reasoning_effort": "none",
            "extra_body": {"custom": True},
        }
    )
    assert settings == {
        "max_tokens": 256,
        "openai_reasoning_effort": "none",
        "extra_body": {"custom": True},
    }


def test_provider_settings_reject_invalid_common_fields() -> None:
    with pytest.raises(ValidationError, match="max_tokens"):
        provider_model_settings({"max_tokens": "unlimited"})


def test_absent_provider_settings_remain_absent() -> None:
    assert provider_model_settings(None) is None
