from __future__ import annotations

import pytest
from types import SimpleNamespace

from typer.testing import CliRunner


@pytest.fixture(autouse=True)
def _isolate_developer_config(tmp_path, monkeypatch):
    """Keep the developer's own Lemma config out of every CLI test.

    The CLI resolves servers from ``~/.lemma/config.json`` when a test does not
    pin one. On a machine that has run Lemma Desktop that file holds a locald
    server, so tests asserting on a config they wrote themselves would instead
    read the developer's, and fail. They passed in CI purely because its home
    directory is empty - the worst kind of green.

    Isolating HOME rather than the individual paths keeps this true for any
    file the CLI learns to read later. LEMMA_* is cleared for the same reason:
    a developer shell that exports one must not steer a test.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    for name in [key for key in list(__import__("os").environ) if key.startswith("LEMMA_")]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def pod_state():
    return SimpleNamespace(
        config={
            "_runtime": {"pod": "pod-1"},
            "defaults": {"org_id": "org-1"},
        },
        output="pretty",
        full=False,
    )


@pytest.fixture
def json_state():
    return SimpleNamespace(
        config={
            "_runtime": {"pod": "pod-1"},
            "defaults": {"org_id": "org-1"},
        },
        output="json",
        full=False,
    )


@pytest.fixture
def patch_run(monkeypatch):
    def _patch(module, *, client, state):
        monkeypatch.setattr(module, "run_with_client", lambda ctx, fn: fn(client, state))

    return _patch
