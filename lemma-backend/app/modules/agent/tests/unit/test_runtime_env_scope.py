"""The runtime profile's `.env` read must not become the process environment.

`load_dotenv` on a whole file writes every variable in a developer's `.env`
into `os.environ` for the life of the process. A pydantic `Settings` reads
`os.environ` even when told to ignore env files, so a local `DEBUG=true`
reached `Settings(environment="production")` and made
`test_debug_is_off_unless_somebody_asks_for_it` — a test about a production
safety rule — fail on a developer's machine while passing in CI, where there is
no `.env`. Two tests failed that way in a full `make test-unit`, and neither
failed on its own.
"""

from __future__ import annotations

import os

import pytest

from app.modules.agent.services import runtime_system_profiles


@pytest.fixture
def dotenv_holding(monkeypatch):
    """Both kinds of variable, as a real developer `.env` carries them."""

    def _use(**values: str):
        monkeypatch.setattr(
            runtime_system_profiles, "dotenv_values", lambda _path: dict(values)
        )

    return _use


def test_only_lemma_variables_reach_the_process(dotenv_holding, monkeypatch):
    for name in ("DEBUG", "DATABASE_URL", "LEMMA_OPENAI_DEFAULT_MODEL"):
        monkeypatch.delenv(name, raising=False)
    dotenv_holding(
        DEBUG="true",
        DATABASE_URL="postgresql://somebody/their-dev-database",
        LEMMA_OPENAI_DEFAULT_MODEL="minimax-m3",
    )

    runtime_system_profiles._load_runtime_env()

    assert os.environ.get("LEMMA_OPENAI_DEFAULT_MODEL") == "minimax-m3"
    leaked = {
        name: os.environ.get(name)
        for name in ("DEBUG", "DATABASE_URL")
        if os.environ.get(name) is not None
    }
    assert leaked == {}, (
        f"a developer's .env reached the process environment: {leaked}. Every "
        f"value this module reads is LEMMA_-prefixed; anything else in scope is "
        f"collateral that later tests and settings objects then read as config"
    )


def test_the_environment_still_wins_over_the_file(dotenv_holding, monkeypatch):
    """`override=False` was the old behaviour and stays the behaviour."""
    monkeypatch.setenv("LEMMA_OPENAI_DEFAULT_MODEL", "set-by-the-operator")
    dotenv_holding(LEMMA_OPENAI_DEFAULT_MODEL="set-by-the-file")

    runtime_system_profiles._load_runtime_env()

    assert os.environ["LEMMA_OPENAI_DEFAULT_MODEL"] == "set-by-the-operator"
