"""Finding a Lemma Desktop install from the state file it writes.

`--server local` resolves its endpoints from locald's `state.json` rather than
from fixed ports, and a gate decides which URLs in that file are believable.
The gate had a hostname spelled into it, which stopped being the only one an
install can serve under: a browser derives no registrable domain from
`*.localhost`, so an install that needs pod apps to work inside the workspace
serves itself under a loopback wildcard instead.

Nothing covered the gate before, and its failure is quiet -- discovery returns
None and the CLI reports the runtime as unavailable, which reads as "Desktop is
not running" rather than "the SDK did not recognise the address".
"""

from __future__ import annotations

import json

import pytest

from lemma_sdk.config import (
    _DESKTOP_LOCAL_BASES,
    _valid_desktop_endpoint,
    _discover_local_server_config,
)


@pytest.mark.parametrize("base", _DESKTOP_LOCAL_BASES)
def test_every_base_desktop_serves_is_recognised(base):
    assert _valid_desktop_endpoint(f"http://app.{base}:52413")
    assert _valid_desktop_endpoint(f"http://app.{base}:52413/")


@pytest.mark.parametrize(
    "value",
    [
        # A port is required: the whole point is that ports are not fixed, and
        # a portless URL means the state file was not written by locald.
        "http://app.lemma.localhost",
        # Not this install. A lookalike suffix is the one that matters -- it is
        # how a name someone else controls would be accepted as local.
        "http://app.lemma.localhost.evil:52413",
        "http://app.127.0.0.1.sslip.io.evil:52413",
        # A different sslip address is somebody else's loopback, not ours.
        "http://app.10.0.0.1.sslip.io:52413",
        # Loopback by literal is not the workspace host, and the workspace host
        # is what the auth URL is built from.
        "http://127.0.0.1:52413",
        "https://app.lemma.localhost:52413",
        "http://user:pw@app.lemma.localhost:52413",
        "http://app.lemma.localhost:52413/admin",
        "http://app.lemma.localhost:52413/?next=x",
        "http://app.lemma.localhost:52413/#f",
        "",
        None,
        1234,
    ],
)
def test_anything_else_is_refused(value):
    assert not _valid_desktop_endpoint(value)


def test_discovery_reads_the_addresses_locald_wrote(tmp_path, monkeypatch):
    """Whatever base the install serves under, both endpoints come from it."""
    for base in _DESKTOP_LOCAL_BASES:
        root = tmp_path / base
        root.mkdir()
        (root / "state.json").write_text(
            json.dumps(
                {
                    "url": f"http://app.{base}:52413",
                    "apiUrl": f"http://app.{base}:52414",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("LEMMA_LOCALD_ROOT", str(root))

        found = _discover_local_server_config()

        assert found is not None, f"an install on {base} was not discovered"
        assert found["base_url"] == f"http://app.{base}:52414"
        assert found["auth_url"] == f"http://app.{base}:52413/auth"


def test_a_state_file_naming_a_host_we_do_not_serve_is_not_discovered(
    tmp_path, monkeypatch
):
    """Discovery is a trust decision, not just a parse.

    The state file is read from disk and its address is handed to a client that
    will send a session to it, so a file naming somewhere else must produce
    nothing rather than a working client pointed off-box.
    """
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "url": "http://app.evil.example:52413",
                "apiUrl": "http://app.evil.example:52414",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LEMMA_LOCALD_ROOT", str(tmp_path))

    assert _discover_local_server_config() is None
