"""Create a GitHub App from the checked-in manifest, with one click.

GitHub's manifest flow cannot be driven by a token: creating an App with
permissions is deliberately gated on a person pressing "Create GitHub App".
What it *can* do is carry every field across, so nobody types a callback URL
into a form and gets it subtly wrong -- which is the failure this exists to
avoid, and one we hit twice by hand.

    python create_github_app.py --name lemma-dev --base-url https://api.asur.work
    python create_github_app.py --name Lemma --base-url https://api.lemma.work \
        --org lemma-work

Open the URL it prints, press the button, and it writes the private key to a
file and prints the env block. The temporary code GitHub returns is single-use
and expires in an hour; nothing is stored anywhere but the files named at the end.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import secrets
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

MANIFEST = (
    pathlib.Path(__file__).resolve().parents[1] / "config" / "github-app-manifest.json"
)
CONVERSION_URL = "https://api.github.com/app-manifests/{code}/conversions"

_result: dict = {}
_done = threading.Event()


def build_manifest(*, name: str, base_url: str, redirect_url: str) -> dict:
    manifest = json.loads(MANIFEST.read_text())
    manifest["name"] = name
    manifest["url"] = base_url
    manifest["redirect_url"] = redirect_url
    # The two URLs the checked-in manifest deliberately leaves out, because they
    # differ per environment and are the whole reason this script exists.
    manifest["hook_attributes"] = {
        **manifest.get("hook_attributes", {}),
        "url": f"{base_url}/webhooks/github",
    }
    manifest["callback_urls"] = [
        f"{base_url}/connectors/connect-requests/oauth/callback"
    ]
    return manifest


def exchange(code: str) -> dict:
    request = urllib.request.Request(
        CONVERSION_URL.format(code=code),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "lemma-create-github-app",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


class _Handler(BaseHTTPRequestHandler):
    manifest: dict = {}
    post_to: str = ""
    state: str = ""

    def log_message(self, *args):  # noqa: D102 - quiet by design
        pass

    def _send(self, body: str, status: int = 200) -> None:
        payload = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
        parsed = urlparse(self.path)
        if parsed.path == "/":
            # A form rather than a redirect: GitHub takes the manifest as a
            # POST body, and it is far too long for a query string.
            self._send(
                "<!doctype html><title>Create the GitHub App</title>"
                f'<form id="f" method="post" action="{self.post_to}">'
                '<input type="hidden" name="manifest" value='
                f"'{json.dumps(self.manifest)}'>"
                "<noscript><button>Continue to GitHub</button></noscript></form>"
                "<script>document.getElementById('f').submit()</script>"
                "<p>Sending you to GitHub…</p>"
            )
            return
        if parsed.path != "/created":
            self._send("<p>Nothing here.</p>", 404)
            return

        query = parse_qs(parsed.query)
        if query.get("state", [""])[0] != self.state:
            self._send("<h1>State mismatch — refusing.</h1>", 400)
            _result["error"] = "state mismatch"
            _done.set()
            return
        code = query.get("code", [""])[0]
        if not code:
            self._send("<h1>GitHub sent no code.</h1>", 400)
            _result["error"] = "no code"
            _done.set()
            return
        try:
            _result.update(exchange(code))
        except Exception as exc:  # noqa: BLE001 - reported, then re-raised below
            _result["error"] = f"{type(exc).__name__}: {exc}"
            self._send("<h1>Exchange failed. See the terminal.</h1>", 500)
            _done.set()
            return
        self._send(
            f"<h1>Created {_result.get('name')}</h1>"
            "<p>The terminal has the credentials. You can close this tab.</p>"
        )
        _done.set()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--base-url", required=True, help="e.g. https://api.asur.work")
    parser.add_argument("--org", default=None, help="omit to create on your account")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--out-dir", default=".")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    redirect_url = f"http://localhost:{args.port}/created"
    _Handler.state = secrets.token_urlsafe(24)
    _Handler.manifest = build_manifest(
        name=args.name, base_url=base_url, redirect_url=redirect_url
    )
    _Handler.post_to = (
        f"https://github.com/organizations/{args.org}/settings/apps/new"
        if args.org
        else "https://github.com/settings/apps/new"
    ) + f"?state={_Handler.state}"

    server = HTTPServer(("127.0.0.1", args.port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    where = f"the {args.org} organization" if args.org else "your account"
    print(f"Creating '{args.name}' on {where}")
    print(f"  webhook  : {base_url}/webhooks/github")
    print(f"  callback : {base_url}/connectors/connect-requests/oauth/callback")
    print(f"  events   : {', '.join(_Handler.manifest['default_events'])}")
    print(
        f"\nOpen this and press 'Create GitHub App':\n\n    http://localhost:{args.port}/\n"
    )

    if not _done.wait(timeout=600):
        print("Timed out waiting for GitHub.", file=sys.stderr)
        return 1
    server.shutdown()

    if "error" in _result:
        print(f"Failed: {_result['error']}", file=sys.stderr)
        return 1

    out = pathlib.Path(args.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    pem_path = out / f"{_result['slug']}.private-key.pem"
    pem_path.write_text(_result["pem"])
    pem_path.chmod(0o600)

    print(f"\nCreated: {_result['html_url']}")
    print(f"Private key: {pem_path}\n")
    print("Put these in the environment's .env:\n")
    print(f"CONNECTOR_GITHUB_CLIENT_ID={_result['client_id']}")
    print(f"CONNECTOR_GITHUB_CLIENT_SECRET={_result['client_secret']}")
    print(f"CONNECTOR_GITHUB_APP_SLUG={_result['slug']}")
    print(f"CONNECTOR_GITHUB_APP_PRIVATE_KEY_PATH={pem_path}")
    print(f"CONNECTOR_GITHUB_APP_WEBHOOK_SECRET={_result['webhook_secret']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
