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
    for name in [
        key for key in list(__import__("os").environ) if key.startswith("LEMMA_")
    ]:
        monkeypatch.delenv(name, raising=False)


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "", None}


def _is_local(address: object) -> bool:
    if isinstance(address, (str, bytes)):  # AF_UNIX path
        return True
    if isinstance(address, tuple) and address:
        host = address[0]
        if host in _LOCAL_HOSTS:
            return True
        return isinstance(host, str) and (
            host.startswith("127.") or host.endswith(".localhost")
        )
    return False


@pytest.fixture(autouse=True)
def _no_outbound_network(request, monkeypatch):
    """No unit test may open a socket to anything but this machine.

    `_isolate_developer_config` keeps the developer's *config* out, and that
    turned out to be the dangerous half of the job: with no config and no
    LEMMA_* left, `resolve_base_url()` falls through to `DEFAULT_BASE_URL`,
    which is `https://api.lemma.work`. **Production is the fallback.** So any
    test that reaches an unstubbed code path does not fail — it quietly calls
    the live service, and the more isolated the test, the more certainly it
    talks to prod rather than to anything local.

    That is not hypothetical. A test that walked every command in the app
    reached `auth login`, which does not go through the stubbed
    `run_with_client`: it fetched `/auth/cli/info` from production, opened a
    real browser, and polled for the full `LOGIN_WAIT_SECONDS` of 300.

    Local addresses stay allowed so a test may still stand up its own server.
    `webbrowser.open` is stopped for the same reason: a test suite must not
    take over the screen.
    """
    if request.node.get_closest_marker("e2e"):
        return

    import socket
    import webbrowser

    real_connect = socket.socket.connect
    real_create = socket.create_connection

    def guard(address: object) -> None:
        if not _is_local(address):
            raise AssertionError(
                f"a unit test tried to reach {address!r}. Nothing outside this "
                "machine is reachable from the unit lane — with no config and "
                "no LEMMA_* set, an unstubbed CLI path resolves to "
                "https://api.lemma.work, so this would have hit production. "
                "Stub the call, or mark the test `e2e`."
            )

    def connect(self, address, *args, **kwargs):
        guard(address)
        return real_connect(self, address, *args, **kwargs)

    def create_connection(address, *args, **kwargs):
        guard(address)
        return real_create(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(
        webbrowser,
        "open",
        lambda *a, **k: pytest.fail("a unit test tried to open a browser"),
    )


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
        monkeypatch.setattr(
            module, "run_with_client", lambda ctx, fn: fn(client, state)
        )

    return _patch
