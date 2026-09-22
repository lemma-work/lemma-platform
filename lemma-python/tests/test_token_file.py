"""`LEMMA_TOKEN_FILE`: a credential a running process can pick up again.

An Agent Host run is handed a delegated session that lives about an hour, and
Lemma refreshes it for runs that outlast that. A process's environment cannot
be rewritten after it is spawned, so an agent given only `LEMMA_TOKEN` keeps
presenting the expired one -- its MCP tools go on working, because the bridge
re-reads the journal, while its own `lemma` commands start failing. The file is
the copy a refresh can reach.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lemma_sdk.config import _token_from_env, should_use_env_server


def test_the_file_is_preferred_over_the_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = tmp_path / "run.token"
    token_file.write_text("refreshed\n", encoding="utf-8")
    monkeypatch.setenv("LEMMA_TOKEN", "the-one-spawned-with")
    monkeypatch.setenv("LEMMA_TOKEN_FILE", str(token_file))

    assert _token_from_env() == "refreshed"


def test_a_rewritten_file_is_seen_without_respawning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This is the whole point of the file."""
    token_file = tmp_path / "run.token"
    token_file.write_text("first", encoding="utf-8")
    monkeypatch.setenv("LEMMA_TOKEN_FILE", str(token_file))
    assert _token_from_env() == "first"

    token_file.write_text("second", encoding="utf-8")
    assert _token_from_env() == "second"


def test_an_unreadable_file_falls_back_rather_than_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEMMA_TOKEN", "still-usable")
    monkeypatch.setenv("LEMMA_TOKEN_FILE", str(tmp_path / "never-written"))

    assert _token_from_env() == "still-usable"


def test_an_empty_file_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = tmp_path / "run.token"
    token_file.write_text("   \n", encoding="utf-8")
    monkeypatch.setenv("LEMMA_TOKEN", "still-usable")
    monkeypatch.setenv("LEMMA_TOKEN_FILE", str(token_file))

    assert _token_from_env() == "still-usable"


def test_a_file_alone_still_selects_the_env_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise a supplied credential is ignored for the stored session."""
    token_file = tmp_path / "run.token"
    token_file.write_text("supplied", encoding="utf-8")
    monkeypatch.delenv("LEMMA_TOKEN", raising=False)
    monkeypatch.setenv("LEMMA_TOKEN_FILE", str(token_file))

    assert should_use_env_server() is True


def test_no_credential_at_all_is_still_no_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LEMMA_TOKEN", raising=False)
    monkeypatch.delenv("LEMMA_TOKEN_FILE", raising=False)

    assert _token_from_env() is None
    assert should_use_env_server() is False


def test_a_token_file_that_is_not_text_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`UnicodeDecodeError` is not an `OSError`.

    A token file that is not UTF-8 raised straight out of the resolver rather
    than falling back to the variable, which is the one case the fallback is
    there for.
    """
    token_file = tmp_path / "run.token"
    token_file.write_bytes(b"\xff\xfe not utf-8 \x00")
    monkeypatch.setenv("LEMMA_TOKEN", "still-usable")
    monkeypatch.setenv("LEMMA_TOKEN_FILE", str(token_file))

    assert _token_from_env() == "still-usable"
