"""A stand-in for the local stack, shaped the way the proof needs it.

Two ports on one process, as Desktop has them:

* the *workspace* port serves the page the proof loads as the top frame;
* the *backend* port signs people in (a ``Domain=lemma.localhost`` session
  cookie, HttpOnly and SameSite=Lax, as SuperTokens is configured locally) and
  serves pod apps by ``Host`` -- ``<slug>.apps.lemma.localhost:<port>`` -- with
  the app's API door at ``/_lemma`` on the app's own origin, as the backend's
  ``AppHostRoutingMiddleware`` does.

The alias in front of the backend is not stood in for: the proof runs locald's
real ``app_alias`` code (``examples/app_alias_serve.rs``). What this stands in
for is the backend, whose own cookie and host-routing behaviour is covered by
its own tests; the question here is only what WebKit does with them.

    python3 stand_in.py <workspace port> <backend port>
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SESSION = "sAccessToken"
TOKEN = "proof-session"
EMAIL = "owner@example.com"
APPS = ".apps.lemma.localhost"

WORKSPACE = """<!doctype html><title>workspace</title><body><script>
(async () => {
  const q = new URLSearchParams(location.search);
  const R = {
    secure: window.isSecureContext,
    mediaDevices: !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia),
    subtle: !!(window.crypto && crypto.subtle),
  };
  const signIn = await fetch(q.get("api") + "/signin", { method: "POST", credentials: "include" });
  R.signIn = signIn.status;
  const got = new Promise(res => addEventListener("message", e => res({ origin: e.origin, data: e.data })));
  const frame = document.createElement("iframe");
  frame.src = q.get("frame");
  document.body.appendChild(frame);
  R.frame = await Promise.race([got, new Promise(r => setTimeout(() => r("timeout"), 8000))]);
  window.__result = R;
})();
</script></body>"""

APP = """<!doctype html><title>app</title><body><script>
(async () => {
  let status = 0, email = null;
  try {
    const r = await fetch("/_lemma/users/me", { credentials: "include" });
    status = r.status;
    if (r.ok) email = (await r.json()).email;
  } catch (e) { status = -1; }
  const R = { status, email, origin: location.origin, framed: window.top !== window };
  if (R.framed) parent.postMessage(R, "*"); else window.__result = R;
})();
</script></body>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="text/html", headers=()):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def signed_in(self):
        cookies = self.headers.get("Cookie", "")
        return f"{SESSION}={TOKEN}" in [part.strip() for part in cookies.split(";")]

    def cors(self):
        origin = self.headers.get("Origin")
        return (
            [
                ("Access-Control-Allow-Origin", origin),
                ("Access-Control-Allow-Credentials", "true"),
            ]
            if origin
            else []
        )

    def do_OPTIONS(self):
        self.send(
            204,
            "",
            headers=self.cors() + [("Access-Control-Allow-Methods", "POST, GET")],
        )

    def do_POST(self):
        if self.path == "/signin":
            cookie = f"{SESSION}={TOKEN}; Domain=lemma.localhost; Path=/; HttpOnly; SameSite=Lax"
            self.send(
                200, "{}", "application/json", self.cors() + [("Set-Cookie", cookie)]
            )
            return
        self.send(404, "no")

    def do_GET(self):
        host = self.headers.get("Host", "").split(":")[0]
        path = self.path.split("?")[0]
        if host.endswith(APPS):
            if path == "/_lemma/users/me":
                if self.signed_in():
                    self.send(200, json.dumps({"email": EMAIL}), "application/json")
                else:
                    self.send(401, "{}", "application/json")
                return
            self.send(200, APP)
            return
        if path == "/":
            self.send(200, WORKSPACE)
            return
        self.send(404, "no")


def main() -> None:
    ports = [int(port) for port in sys.argv[1:3]]
    for port in ports:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
    sys.stdout.write("ready\n")
    sys.stdout.flush()
    sys.stdin.read()


if __name__ == "__main__":
    main()
