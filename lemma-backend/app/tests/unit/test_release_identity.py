"""Strict release identity and compatibility alias coverage."""

from __future__ import annotations

import pytest

from app.core.config import Settings, settings
from app.core.log.log import (
    ReleaseIdentityError,
    release_sha_for_resource,
    validate_release_identity,
)


def test_valid_lowercase_full_sha_is_accepted_in_production(monkeypatch) -> None:
    monkeypatch.setattr(settings, "release_sha", "a" * 40)
    validate_release_identity("production")
    assert release_sha_for_resource() == "a" * 40


@pytest.mark.parametrize("value", [None, "", "abc123", "A" * 40, "g" * 40])
def test_invalid_release_identity_blocks_production_startup(
    monkeypatch, value: str | None
) -> None:
    monkeypatch.setattr(settings, "release_sha", value)
    with pytest.raises(ReleaseIdentityError, match="LEMMA_RELEASE_SHA"):
        validate_release_identity("production")


def test_invalid_development_identity_uses_unknown_without_leaking_value(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "release_sha", "DO-NOT-LOG-THIS")
    validate_release_identity("development")
    assert release_sha_for_resource() == "unknown"


def test_canonical_release_variable_precedes_compatibility_alias(monkeypatch) -> None:
    monkeypatch.setenv("LEMMA_RELEASE_SHA", "a" * 40)
    monkeypatch.setenv("RELEASE_SHA", "b" * 40)
    configured = Settings(_env_file=None)
    assert configured.release_sha == "a" * 40


def test_compatibility_release_variable_remains_supported(monkeypatch) -> None:
    monkeypatch.delenv("LEMMA_RELEASE_SHA", raising=False)
    monkeypatch.setenv("RELEASE_SHA", "b" * 40)
    configured = Settings(_env_file=None)
    assert configured.release_sha == "b" * 40
