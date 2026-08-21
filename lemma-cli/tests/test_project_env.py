"""Server-keyed project ``.lemma.env`` discovery, precedence, and write helper."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lemma_cli.cli_core.project_env import (
    find_project_dir,
    load_project_env,
    write_server_env,
)

NONEXISTENT = Path("/nonexistent-config.json")


@pytest.fixture(autouse=True)
def _isolate_lemma_env(monkeypatch):
    """Start each test with no ambient LEMMA_* (load_project_env writes os.environ
    directly, so snapshot and fully restore around the test)."""
    saved = {k: v for k, v in os.environ.items() if k.startswith("LEMMA_")}
    for key in list(saved):
        monkeypatch.delenv(key, raising=False)
    yield
    for key in [k for k in os.environ if k.startswith("LEMMA_")]:
        del os.environ[key]
    os.environ.update(saved)


def _repo(tmp_path: Path) -> Path:
    root = (tmp_path / "repo").resolve()
    (root / "apps" / "web").mkdir(parents=True)
    (root / ".git").mkdir()
    return root


def _load(start, *, server_flag=None, config_file=NONEXISTENT):
    return load_project_env(
        server_flag=server_flag, config_file=config_file, start=start
    )


# --- discovery ------------------------------------------------------------


def test_anchors_on_base_or_server_file(tmp_path):
    root = _repo(tmp_path)
    (root / ".lemma.lemma-cloud.env").write_text(
        "LEMMA_POD_ID=p\n"
    )  # no base .lemma.env
    assert find_project_dir(root / "apps" / "web") == root


def test_discovery_stops_at_git_root(tmp_path):
    (tmp_path / ".lemma.env").write_text("LEMMA_POD_ID=outer\n")  # above the repo
    root = _repo(tmp_path)
    assert find_project_dir(root / "apps" / "web") is None


def test_discovery_stops_at_home(tmp_path, monkeypatch):
    home = (tmp_path / "home").resolve()
    (home / "proj").mkdir(parents=True)
    (tmp_path / ".lemma.env").write_text("LEMMA_POD_ID=above\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert find_project_dir(home / "proj") is None


def test_no_anchor_is_noop(tmp_path):
    root = _repo(tmp_path)
    (root / ".env").write_text("LEMMA_POD_ID=lone\n")  # a bare .env never anchors
    info = _load(root / "apps" / "web")
    assert info["project_dir"] is None
    assert info["applied"] == []
    assert "LEMMA_POD_ID" not in os.environ


# --- server-keyed precedence ---------------------------------------------


def test_base_default_server_selects_server_file(tmp_path):
    root = _repo(tmp_path)
    (root / ".lemma.env").write_text("LEMMA_SERVER=local\n")
    (root / ".lemma.local.env").write_text("LEMMA_POD_ID=pod_local\n")
    (root / ".lemma.lemma-cloud.env").write_text("LEMMA_POD_ID=pod_cloud\n")
    info = _load(root / "apps" / "web")
    assert info["server"] == "local"
    assert os.environ["LEMMA_POD_ID"] == "pod_local"
    assert os.environ["LEMMA_SERVER"] == "local"


def test_flag_selects_server_file(tmp_path):
    root = _repo(tmp_path)
    (root / ".lemma.env").write_text("LEMMA_SERVER=local\n")
    (root / ".lemma.local.env").write_text("LEMMA_POD_ID=pod_local\n")
    (root / ".lemma.lemma-cloud.env").write_text("LEMMA_POD_ID=pod_cloud\n")
    info = _load(root, server_flag="lemma-cloud")
    assert info["server"] == "lemma-cloud"
    assert os.environ["LEMMA_POD_ID"] == "pod_cloud"


def test_env_lemma_server_selects_server_file(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    (root / ".lemma.local.env").write_text("LEMMA_POD_ID=pod_local\n")
    monkeypatch.setenv("LEMMA_SERVER", "local")
    info = _load(root)
    assert info["server"] == "local"
    assert os.environ["LEMMA_POD_ID"] == "pod_local"


def test_config_active_server_selects_server_file(tmp_path):
    root = _repo(tmp_path)
    (root / ".lemma.prod.env").write_text("LEMMA_POD_ID=pod_prod\n")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"active_server": "prod", "servers": {"prod": {"defaults": {}}}})
    )
    info = _load(root, config_file=config)
    assert info["server"] == "prod"
    assert os.environ["LEMMA_POD_ID"] == "pod_prod"


def test_server_file_overrides_base(tmp_path):
    root = _repo(tmp_path)
    (root / ".lemma.env").write_text("LEMMA_SERVER=local\nLEMMA_POD_ID=base_pod\n")
    (root / ".lemma.local.env").write_text("LEMMA_POD_ID=server_pod\n")
    _load(root)
    assert os.environ["LEMMA_POD_ID"] == "server_pod"


def test_local_variants_win(tmp_path):
    root = _repo(tmp_path)
    (root / ".lemma.env").write_text("LEMMA_SERVER=local\n")
    (root / ".lemma.local.env").write_text("LEMMA_POD_ID=committed\n")
    (root / ".lemma.local.env.local").write_text("LEMMA_POD_ID=personal\n")
    _load(root)
    assert os.environ["LEMMA_POD_ID"] == "personal"


def test_only_lemma_keys_applied(tmp_path):
    root = _repo(tmp_path)
    (root / ".lemma.env").write_text("LEMMA_POD_ID=p\nOPENAI_API_KEY=sk-x\n")
    info = _load(root)
    assert "OPENAI_API_KEY" not in os.environ
    assert info["applied"] == ["LEMMA_POD_ID"]


def test_real_env_wins(tmp_path):
    root = _repo(tmp_path)
    (root / ".lemma.env").write_text("LEMMA_SERVER=local\n")
    (root / ".lemma.local.env").write_text("LEMMA_POD_ID=from_file\n")
    os.environ["LEMMA_POD_ID"] = "from_shell"
    info = _load(root)
    assert os.environ["LEMMA_POD_ID"] == "from_shell"
    assert "LEMMA_POD_ID" not in info["applied"]


def test_real_token_skips_all_files(tmp_path):
    root = _repo(tmp_path)
    (root / ".lemma.env").write_text("LEMMA_SERVER=local\n")
    (root / ".lemma.local.env").write_text("LEMMA_POD_ID=file_pod\n")
    os.environ["LEMMA_TOKEN"] = "real-token"
    os.environ["LEMMA_POD_ID"] = "real_pod"
    info = _load(root)
    assert info["applied"] == []
    assert info["skipped_reason"]
    assert os.environ["LEMMA_POD_ID"] == "real_pod"
    assert "LEMMA_SERVER" not in os.environ


def test_token_in_committed_base_flagged(tmp_path):
    root = _repo(tmp_path)
    (root / ".lemma.env").write_text("LEMMA_TOKEN=tok\nLEMMA_POD_ID=p\n")
    info = _load(root)
    assert info["token_in_committed_file"] is True


# --- write helper ---------------------------------------------------------


def test_write_server_env_writes_binding_and_seeds_default(tmp_path):
    root = (tmp_path / "bundle").resolve()
    root.mkdir()
    path = write_server_env(
        root, "lemma-cloud", {"LEMMA_POD_ID": "p1", "LEMMA_ORG_ID": "o1"}
    )
    assert path.name == ".lemma.lemma-cloud.env"
    text = path.read_text()
    assert "LEMMA_POD_ID=p1" in text and "LEMMA_ORG_ID=o1" in text
    # seeds the project's default server when .lemma.env has none
    assert "LEMMA_SERVER=lemma-cloud" in (root / ".lemma.env").read_text()


def test_write_server_env_merges_and_keeps_existing_default(tmp_path):
    root = (tmp_path / "bundle").resolve()
    root.mkdir()
    (root / ".lemma.env").write_text("LEMMA_SERVER=local\n")
    write_server_env(root, "lemma-cloud", {"LEMMA_POD_ID": "p1"})
    write_server_env(root, "lemma-cloud", {"LEMMA_ORG_ID": "o1"})
    text = (root / ".lemma.lemma-cloud.env").read_text()
    assert "LEMMA_POD_ID=p1" in text and "LEMMA_ORG_ID=o1" in text  # merged
    # does not clobber an existing default server
    assert "LEMMA_SERVER=local" in (root / ".lemma.env").read_text()
    # round-trips through the loader
    for key in [k for k in os.environ if k.startswith("LEMMA_")]:
        del os.environ[key]
    info = _load(root, server_flag="lemma-cloud")
    assert os.environ["LEMMA_POD_ID"] == "p1"
    assert os.environ["LEMMA_ORG_ID"] == "o1"
    assert info["server"] == "lemma-cloud"
