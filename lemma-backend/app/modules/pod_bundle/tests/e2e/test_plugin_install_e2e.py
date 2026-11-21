"""E2E: several plugins go into one Lemma from one request, with nobody watching.

``POST /pods/{pod_id}/bundle/installs`` names plugins -- folders of one GitHub
repository here -- and returns at once. The work is then run in-process to the
end (``run_until_idle``) without a single status read in between: what is
installed is what the server did on its own. Real Postgres and the real API;
GitHub is a local server that hands out one repository's zipball.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work import WorkClass
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.pod_bundle.config import pod_bundle_settings
from app.modules.pod_bundle.tests.e2e.bundle_e2e_helpers import new_pod
from app.modules.pod_bundle.tests.e2e.bundle_work_support import (
    COLUMNS,
    rows,
    table_names,
)
from lemma_pod_bundle import pack_bundle

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

REPOSITORY = "https://github.com/acme/plugins"


def _write(path: Path, body: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if isinstance(body, str) else json.dumps(body))


def _plugin(root: Path, *, name: str, table: str, extra: dict[str, object]) -> None:
    _write(root / "pod.json", {"name": name, "format_version": 4, "variables": {}})
    _write(
        root / "tables" / table / f"{table}.json",
        {"name": table, "primary_key_column": "id", "columns": COLUMNS},
    )
    for path, body in extra.items():
        _write(root / path, body)


def _repository(tmp_path: Path) -> bytes:
    """A repository of plugins, zipped as GitHub zips one: every file under one
    `owner-repo-sha/` folder.

    - ``mail`` and ``crm``: a table each, nothing to ask.
    - ``ledger``: needs a connector nobody can install here -- its page asks.
    - ``guides``: a table, then a file inside a ``docs`` folder.
    """
    top = tmp_path / f"archive-{uuid4().hex[:6]}" / "acme-plugins-1a2b3c4"
    _plugin(top / "install" / "mail", name="Mail", table="threads", extra={})
    _plugin(top / "install" / "crm", name="CRM", table="deals", extra={})
    _plugin(
        top / "install" / "ledger",
        name="Ledger",
        table="entries",
        extra={
            "connectors/books/books.json": {
                "connector": f"no-such-connector-{uuid4().hex[:6]}",
                "mode": "PER_MEMBER",
                "required": True,
            }
        },
    )
    _plugin(
        top / "install" / "guides",
        name="Guides",
        table="pages",
        extra={
            "files/docs/.folder.json": {"visibility": "POD"},
            "files/docs/start.md": "Read me.",
        },
    )
    return pack_bundle(top.parent)


@pytest.fixture
def served(tmp_path, monkeypatch) -> Iterator[list[str]]:
    """Serve the repository's zipball, for any repository, from a local
    server the import's GitHub API base points at; record each fetch."""
    archive = _repository(tmp_path)
    fetched: list[str] = []

    class Zipball(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - the stdlib's name
            if "/zipball" not in self.path:
                self.send_response(404)
                self.end_headers()
                return
            fetched.append(self.path.split("/zipball")[0].removeprefix("/repos/"))
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(archive)))
            self.end_headers()
            self.wfile.write(archive)

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Zipball)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(
        pod_bundle_settings,
        "pod_bundle_github_api_base",
        f"http://127.0.0.1:{server.server_address[1]}",
    )
    try:
        yield fetched
    finally:
        server.shutdown()
        server.server_close()


async def _install(client, pod_id: str, *folders: str) -> dict:
    res = await client.post(
        f"/pods/{pod_id}/bundle/installs",
        json={"plugins": [{"repo_url": REPOSITORY, "path": f} for f in folders]},
    )
    assert res.status_code == status.HTTP_202_ACCEPTED, res.text
    return res.json()


async def _read(client, pod_id: str, install_id: str) -> dict:
    res = await client.get(f"/pods/{pod_id}/bundle/installs/{install_id}")
    assert res.status_code == status.HTTP_200_OK, res.text
    return res.json()


async def _run_everything() -> None:
    await run_until_idle(async_session_maker, classes=[WorkClass.ADMIN])


async def _notes(pod_id: str, user_id: str) -> list[str]:
    """What the Lemma said in the person's own chat with it."""
    found = await rows(
        "SELECT m.body FROM channel_messages m "
        "JOIN channels c ON c.id = m.channel_id "
        "WHERE c.pod_id = :p AND c.kind = 'DIRECT' AND c.member_user_id = :u "
        "ORDER BY m.seq",
        p=UUID(pod_id),
        u=UUID(user_id),
    )
    return [row[0] for row in found]


async def test_every_plugin_goes_in_in_order_with_nobody_polling(
    authenticated_client, fixed_test_org, fixed_test_user, served
):
    client = authenticated_client
    pod_id = await new_pod(client, fixed_test_org["id"], label="Install")
    started = await _install(client, pod_id, "install/mail", "install/crm")

    assert started["finished"] is False
    assert [(p["path"], p["state"]) for p in started["plugins"]] == [
        ("install/mail", "WAITING"),
        ("install/crm", "WAITING"),
    ]

    await _run_everything()

    assert served == ["acme/plugins", "acme/plugins"]
    assert {"threads", "deals"} <= set(await table_names(pod_id))
    done = await _read(client, pod_id, started["install_id"])
    assert done["finished"] is True
    assert [(p["name"], p["state"]) for p in done["plugins"]] == [
        ("Mail", "INSTALLED"),
        ("CRM", "INSTALLED"),
    ]
    assert all(
        p["progress"]["done"] == p["progress"]["total"] > 0 for p in done["plugins"]
    )
    # Each is an ordinary import, readable on its own.
    first = await client.get(
        f"/pods/{pod_id}/bundle/imports/{done['plugins'][0]['import_id']}"
    )
    assert first.json()["status"] == "COMPLETED", first.text
    # In the order named: the first was finished before the second started.
    finished = await rows(
        "SELECT snapshot->'install'->>'position', completed_at, created_at "
        "FROM pod_bundle_jobs WHERE pod_id = :p ORDER BY 1",
        p=UUID(pod_id),
    )
    assert finished[0][1] is not None and finished[1][1] is not None
    assert finished[0][1] < finished[1][1]

    assert await _notes(pod_id, fixed_test_user["id"]) == ["Mail and CRM are ready."]


async def test_a_plugin_whose_page_would_ask_stops_and_the_next_goes_in(
    authenticated_client, fixed_test_org, fixed_test_user, served
):
    client = authenticated_client
    pod_id = await new_pod(client, fixed_test_org["id"], label="Install")
    started = await _install(client, pod_id, "install/ledger", "install/mail")

    await _run_everything()

    done = await _read(client, pod_id, started["install_id"])
    ledger, mail = done["plugins"]
    assert (ledger["state"], mail["state"]) == ("NEEDS_ANSWER", "INSTALLED")
    # The page's own words for the connector it cannot map.
    assert "no-such-connector" in ledger["reason"]
    assert "installation" in ledger["reason"]
    # Withdrawn before anything of it was applied.
    tables = await table_names(pod_id)
    assert "entries" not in tables and "threads" in tables
    connectors = await client.get(f"/pods/{pod_id}/connectors")
    assert connectors.json()["items"] == [], connectors.text
    imported = await client.get(f"/pods/{pod_id}/bundle/imports/{ledger['import_id']}")
    assert imported.json()["status"] == "CANCELLED"

    [note] = await _notes(pod_id, fixed_test_user["id"])
    assert note.startswith("Mail is ready.\nLedger didn't go in.")


async def test_a_failed_plugin_carries_on_from_its_failed_step_on_retry(
    authenticated_client, fixed_test_org, fixed_test_user, served
):
    client = authenticated_client
    pod_id = await new_pod(client, fixed_test_org["id"], label="Install")
    # A file where the plugin has a folder: its file inside cannot be written.
    blocker = await client.post(
        f"/pods/{pod_id}/datastore/files",
        data={"directory_path": "/", "search_enabled": "false"},
        files={"data": ("docs", b"in the way", "text/plain")},
    )
    assert blocker.status_code == status.HTTP_201_CREATED, blocker.text
    started = await _install(client, pod_id, "install/guides")
    install_id = started["install_id"]

    await _run_everything()

    [failed] = (await _read(client, pod_id, install_id))["plugins"]
    assert failed["state"] == "FAILED", failed
    assert failed["reason"]
    steps = (
        await client.get(f"/pods/{pod_id}/bundle/imports/{failed['import_id']}")
    ).json()["plan"]["steps"]
    by_name = {(s["kind"], s["name"]): s["status"] for s in steps}
    assert by_name[("TABLE", "pages")] == "DONE"
    [told] = await _notes(pod_id, fixed_test_user["id"])
    assert told.startswith("Guides didn't finish.")

    cleared = await client.delete(
        f"/pods/{pod_id}/datastore/files/by-path", params={"path": "/docs"}
    )
    assert cleared.status_code == status.HTTP_204_NO_CONTENT, cleared.text
    retried = await client.post(f"/pods/{pod_id}/bundle/installs/{install_id}/retry")
    assert retried.status_code == status.HTTP_202_ACCEPTED, retried.text
    assert retried.json()["plugins"][0]["import_id"] == failed["import_id"]

    await _run_everything()

    [again] = (await _read(client, pod_id, install_id))["plugins"]
    assert again["state"] == "INSTALLED", again
    # The same import, carried on: the table was not made again.
    after = (
        await client.get(f"/pods/{pod_id}/bundle/imports/{failed['import_id']}")
    ).json()
    assert after["status"] == "COMPLETED"
    assert after["committed_steps"][0] == next(
        s["index"] for s in steps if s["name"] == "pages"
    )
    assert (await table_names(pod_id)).count("pages") == 1
    assert await _notes(pod_id, fixed_test_user["id"]) == [
        told,
        "Guides is ready.",
    ]


async def test_an_install_is_its_starters_and_names_each_plugin_once(
    authenticated_client, fixed_test_org
):
    client = authenticated_client
    pod_id = await new_pod(client, fixed_test_org["id"], label="Install")
    twice = await client.post(
        f"/pods/{pod_id}/bundle/installs",
        json={
            "plugins": [
                {"repo_url": REPOSITORY, "path": "install/mail"},
                {"owner": "acme", "repo": "plugins", "path": "install/mail"},
            ]
        },
    )
    assert twice.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY, twice.text
    unknown = await client.get(f"/pods/{pod_id}/bundle/installs/{uuid4()}")
    assert unknown.status_code == status.HTTP_404_NOT_FOUND, unknown.text
