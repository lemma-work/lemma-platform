"""Round-2: default-server bootstrap and session selection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lemma_sdk.config import (
    DEFAULT_AUTH_URL,
    DEFAULT_BASE_URL,
    get_server_config,
    load_config,
    normalize_server_config,
    resolve_base_url,
)

from lemma_cli.cli_core.context import render_session_selection
from lemma_cli.cli_core.state import CliState


def _state(output: str = "pretty") -> CliState:
    return CliState(
        config_path=Path("/tmp/none.json"),
        config={},
        base_url=None,
        auth_url=None,
        token=None,
        timeout=30.0,
        no_verify_ssl=False,
        output=output,
    )


def test_fresh_config_seeds_and_selects_cloud_server(tmp_path):
    cfg = load_config(tmp_path / "does-not-exist.json")
    assert cfg["active_server"] == "lemma-cloud"
    cloud = cfg["servers"]["lemma-cloud"]
    assert cloud["base_url"] == DEFAULT_BASE_URL == "https://api.lemma.work"
    assert cloud["auth_url"] == DEFAULT_AUTH_URL == "https://lemma.work/auth"


def test_legacy_default_server_migrates_to_lemma_cloud(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "active_server": "default",
                "servers": {"default": {"base_url": "http://custom", "defaults": {"pod_id": "p"}}},
            }
        ),
        encoding="utf-8",
    )
    loaded = load_config(cfg)
    assert loaded["active_server"] == "lemma-cloud"
    assert "default" not in loaded["servers"]
    # a user-customized URL is preserved verbatim under the new name
    assert loaded["servers"]["lemma-cloud"]["base_url"] == "http://custom"
    assert loaded["servers"]["lemma-cloud"]["defaults"]["pod_id"] == "p"


def test_migration_skips_when_lemma_cloud_already_present(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "active_server": "default",
                "servers": {"default": {"base_url": "d"}, "lemma-cloud": {"base_url": "c"}},
            }
        ),
        encoding="utf-8",
    )
    loaded = load_config(cfg)
    # never clobber an existing lemma-cloud; leave the legacy default in place
    assert "default" in loaded["servers"]
    assert loaded["servers"]["lemma-cloud"]["base_url"] == "c"


def test_local_server_discovers_dynamic_desktop_endpoints(tmp_path, monkeypatch):
    locald = tmp_path / "locald"
    locald.mkdir()
    (locald / "state.json").write_text(
        json.dumps(
            {
                "url": "http://app.lemma.localhost:52123",
                "apiUrl": "http://app.lemma.localhost:58765",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LEMMA_LOCALD_ROOT", str(locald))
    root, selected = normalize_server_config(
        load_config(tmp_path / "missing.json"),
        selected_server="local",
    )

    local = get_server_config(root, selected)

    assert resolve_base_url(None, local, use_env=False) == (
        "http://app.lemma.localhost:58765"
    )
    assert local["auth_url"] == "http://app.lemma.localhost:52123/auth"
    assert local["_sources"]["base_url"].endswith("state.json")


def test_local_server_refreshes_a_stale_registered_endpoint(tmp_path, monkeypatch):
    locald = tmp_path / "locald"
    locald.mkdir()
    (locald / "state.json").write_text(
        json.dumps(
            {
                "url": "http://app.lemma.localhost:52123",
                "apiUrl": "http://app.lemma.localhost:58765",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LEMMA_LOCALD_ROOT", str(locald))
    root, selected = normalize_server_config(
        {
            "active_server": "local",
            "servers": {
                "local": {
                    "base_url": "http://app.lemma.localhost:8711",
                    "auth_url": "http://app.lemma.localhost:3711/auth",
                }
            },
        },
        selected_server="local",
    )

    local = get_server_config(root, selected)

    assert local["base_url"] == "http://app.lemma.localhost:58765"
    assert local["auth_url"] == "http://app.lemma.localhost:52123/auth"


def test_local_server_never_silently_falls_back_to_cloud(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMA_LOCALD_ROOT", str(tmp_path / "missing-locald"))
    root, selected = normalize_server_config(
        load_config(tmp_path / "missing.json"),
        selected_server="local",
    )

    local = get_server_config(root, selected)

    with pytest.raises(ValueError, match="Desktop local endpoints are unavailable"):
        resolve_base_url(None, local, use_env=False)


def test_session_selection_export_mode_emits_eval_safe_lines(capsys):
    render_session_selection(
        _state(),
        env={"LEMMA_POD_ID": "pod-1", "LEMMA_ORG_ID": "org-1"},
        label="pod",
        name="linear-pm",
        command_hint="lemma pods select linear-pm",
        export_only=True,
        saved=False,
    )
    out = capsys.readouterr().out.strip().splitlines()
    # Export mode prints ONLY plain `export …` lines (so `eval "$(...)"` is safe).
    assert out == ['export LEMMA_POD_ID=pod-1', 'export LEMMA_ORG_ID=org-1']


def test_session_selection_human_mode_shows_session_hint(capsys):
    render_session_selection(
        _state(),
        env={"LEMMA_POD_ID": "pod-1"},
        label="pod",
        name="linear-pm",
        command_hint="lemma pods select linear-pm",
        export_only=False,
        saved=False,
    )
    out = capsys.readouterr().out
    assert "this shell only" in out
    assert 'eval "$(lemma pods select linear-pm -x)"' in out


def test_humanize_error_unwraps_422_field_errors():
    from lemma_sdk.errors import api_error

    from lemma_cli.cli_core.state import humanize_error

    err = api_error(
        422,
        "Validation error",
        details=[
            {
                "loc": ["body", "column"],
                "msg": "ENUM columns must define options",
                "type": "value_error",
            }
        ],
    )
    out = humanize_error(err)
    assert "Validation error" in out
    assert "column: ENUM columns must define options" in out
    assert "loc" not in out  # no raw {'loc': ...} dict dump
