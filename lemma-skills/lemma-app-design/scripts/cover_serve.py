#!/usr/bin/env python3
"""Serve a built Lemma app on localhost with sample data and no way to real data.

    python3 cover_serve.py --app ./dist --fixtures /tmp/cover/fixtures --port 4610

What makes the render safe is how the page is wired, not anyone's care:

- The entry page is given ``window.__LEMMA_CONFIG__`` with ``apiUrl: "/_lemma"``,
  so every SDK call goes to this server, and a placeholder token, so the SDK
  starts signed in without a session. The token opens nothing anywhere.
- ``/_lemma/...`` is answered from JSON files under ``--fixtures`` and from
  nothing else. This server holds no credentials and forwards no request to the
  API, except the SDK's own public script (``/_lemma/public/...``), fetched
  with no headers.

A fixture file is the request path under ``/_lemma/``, plus ``.json``:

    GET  /_lemma/pods/pod/datastore/tables/deals/records -> pods/pod/datastore/tables/deals/records.json
    POST /_lemma/pods/pod/datastore/query                -> pods/pod/datastore/query.json

A ``query.json`` may hold a list of ``{"match": "<text in the SQL>", "response": {...}}``
to answer different queries differently; the first match wins.

The sign-in calls answer with a sample person by default. A path with no
fixture answers an empty list and is printed as ``MISS``: if the screen is
empty where it should not be, the MISS lines say which files to write.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import PurePosixPath
from urllib.parse import urlsplit

POD_ID = "pod"
API_PREFIX = "/_lemma/"
TOKEN = "cover-preview"

SAMPLE_USER = {
    "id": "user-sample",
    "email": "sam@example.com",
    "first_name": "Sam",
    "last_name": "Rivera",
    "is_active": True,
    "is_superuser": False,
    "is_verified": True,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}
SAMPLE_MEMBER = {
    "pod_member_id": "member-sample",
    "user_id": SAMPLE_USER["id"],
    "email": SAMPLE_USER["email"],
    "user_email": SAMPLE_USER["email"],
    "roles": ["POD_EDITOR"],
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}
EMPTY_LIST = {"items": [], "limit": 0, "next_page_token": None, "total": 0}


def say(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def default_answer(path: str) -> object | None:
    if path == "users/me":
        return SAMPLE_USER
    if path.startswith(f"pods/{POD_ID}/members/lookup/by-user-id/"):
        return SAMPLE_MEMBER
    if path.startswith("health/"):
        return {"status": "ok"}
    return None


def config_script(name: str) -> bytes:
    config = {
        "podId": POD_ID,
        "apiUrl": API_PREFIX.rstrip("/"),
        "authUrl": "about:blank",
        "app": {"name": name},
    }
    return (
        "<script>"
        f"window.__LEMMA_CONFIG__={json.dumps(config)};"
        f"try{{localStorage.setItem('lemma_token',{json.dumps(TOKEN)})}}catch(e){{}}"
        "</script>"
    ).encode("utf-8")


class Handler(SimpleHTTPRequestHandler):
    # Real paths, so ``contained`` can compare a request's against them.
    app_dir: str
    fixtures: str
    api_base: str
    app_name: str

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        self._route()

    def do_HEAD(self) -> None:
        self._route()

    def do_POST(self) -> None:
        self._route()

    def do_PATCH(self) -> None:
        self._route()

    def do_PUT(self) -> None:
        self._route()

    def do_DELETE(self) -> None:
        self._route()

    def _route(self) -> None:
        path = urlsplit(self.path).path
        if path.startswith(API_PREFIX):
            self._api(path[len(API_PREFIX) :])
        elif self.command in {"GET", "HEAD"}:
            self._app(path)
        else:
            self._send(405, b"", "text/plain")

    def _api(self, path: str) -> None:
        body = b""
        length = int(self.headers.get("content-length") or 0)
        if length:
            body = self.rfile.read(length)
        if path.startswith("public/"):
            self._public(path)
            return
        answer = self._fixture(path, body)
        if answer is None:
            answer = default_answer(path)
        if answer is None:
            say(f"MISS {self.command} {API_PREFIX}{path}")
            answer = EMPTY_LIST
        self._send(200, json.dumps(answer).encode("utf-8"), "application/json")

    def _fixture(self, path: str, body: bytes) -> object | None:
        file = contained(self.fixtures, f"{path}.json")
        if file is None or not os.path.isfile(file):
            return None
        with open(file, encoding="utf-8") as handle:
            data = json.load(handle)
        if (
            isinstance(data, list)
            and data
            and all(isinstance(entry, dict) and "match" in entry for entry in data)
        ):
            sql = body.decode("utf-8", "replace").lower()
            for entry in data:
                if str(entry["match"]).lower() in sql:
                    return entry.get("response", EMPTY_LIST)
            say(f"MISS {self.command} {API_PREFIX}{path} (no match for this query)")
            return EMPTY_LIST
        return data

    def _public(self, path: str) -> None:
        """The SDK script a no-build app loads. Public, fetched with no headers."""
        request = urllib.request.Request(f"{self.api_base}/{path}", headers={})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                content = response.read()
                media = response.headers.get("content-type", "text/javascript")
        except OSError as error:
            say(f"SDK {path}: {error}")
            self._send(502, b"", "text/plain")
            return
        self._send(200, content, media)

    def _app(self, path: str) -> None:
        name = path.lstrip("/")
        file = contained(self.app_dir, name or "index.html")
        if file is not None and os.path.isdir(file):
            file = contained(self.app_dir, os.path.join(name, "index.html"))
        if file is None or not os.path.isfile(file):
            # A missing file is a missing asset; a path with no extension is
            # the app's own client-side route, which its index.html answers.
            if "." in PurePosixPath(name).name:
                self._send(404, b"", "text/plain")
                return
            file = contained(self.app_dir, "index.html")
        if file is None:
            self._send(404, b"", "text/plain")
            return
        with open(file, "rb") as handle:
            content = handle.read()
        if os.path.basename(file) == "index.html":
            content = inject(content, config_script(self.app_name))
        self._send(200, content, self.guess_type(file))

    def _send(self, status: int, content: bytes, media: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)


def contained(root: str, relative: str) -> str | None:
    """``relative`` resolved under ``root``, or None if it would leave it.

    Every path a request names goes through here before it touches the disk,
    so ``..``, an absolute path or a symlink out of the folder answers nothing.
    """
    candidate = os.path.realpath(os.path.join(root, relative))
    if candidate != root and not candidate.startswith(root + os.sep):
        return None
    return candidate


def inject(page: bytes, script: bytes) -> bytes:
    """Put ``script`` first in <head>, so it runs before the app's own."""
    lowered = page.lower()
    head = lowered.find(b"<head")
    if head != -1:
        end = page.find(b">", head)
        if end != -1:
            return page[: end + 1] + script + page[end + 1 :]
    return script + page


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--app", required=True, help="the built app: dist/, or an HTML app's folder"
    )
    parser.add_argument(
        "--fixtures", required=True, help="folder of sample JSON responses"
    )
    parser.add_argument("--port", type=int, default=4610)
    parser.add_argument(
        "--name", default="App", help="the app's name, as the page is told it"
    )
    parser.add_argument(
        "--api",
        default=os.environ.get("LEMMA_BASE_URL", "https://api.lemma.work"),
        help="where the public SDK script is fetched from (no credentials are sent)",
    )
    args = parser.parse_args()

    app_dir = os.path.realpath(args.app)
    if not os.path.isfile(os.path.join(app_dir, "index.html")):
        say(f"{app_dir} has no index.html: build the app first")
        return 2
    Handler.app_dir = app_dir
    Handler.fixtures = os.path.realpath(args.fixtures)
    Handler.api_base = args.api.rstrip("/")
    Handler.app_name = args.name

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    say(f"serving {app_dir} with sample data at http://localhost:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
