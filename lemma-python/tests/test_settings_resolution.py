"""Where the SDK dials and what it sends must be chosen together.

Resolving the endpoint and the credential from independent sources is how a
token minted for one host is sent to another; the CLI has always picked a server
first and read both from it, and these pin the SDK to the same rule.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lemma_sdk.errors import LemmaConfigError
from lemma_sdk.settings import load_settings

CLOUD = "https://api.example-cloud.test"
SELF_HOSTED = "http://self-hosted.example.test:8080"


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "LEMMA_TOKEN",
        "LEMMA_BASE_URL",
        "LEMMA_SERVER",
        "LEMMA_ORG_ID",
        "LEMMA_POD_ID",
        "LEMMA_AUTH_URL",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "active_server": "self-hosted",
                "servers": {
                    "self-hosted": {
                        "base_url": SELF_HOSTED,
                        "token": "token-for-self-hosted",
                        "defaults": {"pod_id": "pod-from-config"},
                    }
                },
            }
        )
    )
    return path


def test_env_token_is_never_paired_with_the_config_files_server(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEMMA_TOKEN", "token-for-cloud")

    settings = load_settings(config_path=config_file)

    # Before: base_url came from the active server while the token came from the
    # environment, so a cloud credential was sent to the self-hosted host.
    assert settings.token == "token-for-cloud"
    assert settings.base_url != SELF_HOSTED
    assert settings.server == "env"


def test_env_token_pairs_with_the_env_base_url(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEMMA_TOKEN", "token-for-cloud")
    monkeypatch.setenv("LEMMA_BASE_URL", CLOUD)

    settings = load_settings(config_path=config_file)

    assert (settings.base_url, settings.token) == (CLOUD, "token-for-cloud")


def test_naming_a_server_uses_that_servers_own_credential(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Asking for a server by name means that server's endpoint *and* its stored
    # token; the environment does not get to redirect either half.
    monkeypatch.setenv("LEMMA_TOKEN", "token-for-cloud")
    monkeypatch.setenv("LEMMA_BASE_URL", CLOUD)

    settings = load_settings(server="self-hosted", config_path=config_file)

    assert settings.base_url == SELF_HOSTED
    assert settings.token == "token-for-self-hosted"


def test_explicit_arguments_win_over_every_source(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEMMA_TOKEN", "token-for-cloud")

    settings = load_settings(
        base_url="https://explicit.example.test",
        token="explicit-token",
        config_path=config_file,
    )

    assert settings.base_url == "https://explicit.example.test"
    assert settings.token == "explicit-token"


def test_config_server_is_used_when_no_env_token_is_set(config_file: Path) -> None:
    settings = load_settings(config_path=config_file)

    assert settings.base_url == SELF_HOSTED
    assert settings.token == "token-for-self-hosted"
    assert settings.pod_id == "pod-from-config"


def test_missing_token_names_the_three_ways_to_supply_one(tmp_path: Path) -> None:
    with pytest.raises(LemmaConfigError) as excinfo:
        load_settings(config_path=tmp_path / "absent.json")

    assert "LEMMA_TOKEN" in str(excinfo.value)


def test_a_config_refresh_token_is_carried_into_settings(config_file: Path) -> None:
    """`LEMMA_REFRESH_TOKEN` was documented and inert.

    It reached the synthetic ``env`` server config and stopped there: nothing
    read it, so a long-lived process using the SDK simply began failing when its
    access token aged out. The transport's 401 branch is what uses this.
    """
    config_file.write_text(
        json.dumps(
            {
                "active_server": "self-hosted",
                "servers": {
                    "self-hosted": {
                        "base_url": SELF_HOSTED,
                        "auth": {
                            "access_token": "access-1",
                            "refresh_token": "refresh-1",
                        },
                    }
                },
            }
        )
    )

    settings = load_settings(config_path=config_file)

    assert settings.token == "access-1"
    assert settings.refresh_token == "refresh-1"


def test_an_env_refresh_token_is_carried_into_settings(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The environment pair works the same way the config pair does.

    The CLI refuses to refresh an env-sourced session because it has nowhere to
    persist the result. The SDK holds its transport in memory for the life of
    the process, which is exactly the scope a refreshed token needs, so there is
    nothing to persist and nothing to refuse.
    """
    monkeypatch.setenv("LEMMA_TOKEN", "access-from-env")
    monkeypatch.setenv("LEMMA_REFRESH_TOKEN", "refresh-from-env")

    settings = load_settings(config_path=config_file)

    assert settings.server == "env"
    assert settings.refresh_token == "refresh-from-env"


def test_an_explicit_token_gets_no_refresh_token(config_file: Path) -> None:
    """A caller that passed `token=` owns that credential.

    Pairing their token with a refresh token from a config file they did not
    name would mean the SDK quietly replacing what they supplied, with a
    credential for whatever server that file happens to select.
    """
    config_file.write_text(
        json.dumps(
            {
                "active_server": "self-hosted",
                "servers": {
                    "self-hosted": {
                        "base_url": SELF_HOSTED,
                        "auth": {
                            "access_token": "access-1",
                            "refresh_token": "refresh-1",
                        },
                    }
                },
            }
        )
    )

    settings = load_settings(token="caller-supplied", config_path=config_file)

    assert settings.token == "caller-supplied"
    assert settings.refresh_token is None
