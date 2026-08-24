"""Runs inside mitmproxy, and is the only thing in this suite that may import it.

The suite talks to this over HTTP rather than importing mitmproxy itself, for two
reasons. The scenario suite keeps a deliberately thin dependency list — `httpx`
and little else — and mitmproxy is a large tree with its own pinned
`cryptography`. And the proxy has to be a *process* the stack is configured to
use, not a library the test imports: the suite's contract is that nothing is
patched, and a proxy the product opted into via `HTTPS_PROXY` is the same kind
of seam as the database it was pointed at.

What it does: remember every request that went out and what came back, and
answer questions about them on a control port.

    GET  /calls            everything, newest last
    POST /reset            forget it all — one scenario's traffic is not another's
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from mitmproxy import ctx, http

#: Bounded, because a long run against a chatty provider would otherwise grow
#: without limit inside a process nobody is watching. Ten thousand calls is far
#: more than any scenario makes and far less than a memory problem.
KEEP = 10_000


class Recorder:
    """Everything Lemma sent out, and what came back."""

    def __init__(self) -> None:
        self._calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._server: HTTPServer | None = None

    # --- mitmproxy hooks --------------------------------------------------

    def load(self, loader) -> None:
        loader.add_option(
            name="control_port",
            typespec=int,
            default=0,
            help="Port the suite asks about recorded calls on.",
        )

    def running(self) -> None:
        if self._server is not None:
            return
        self._server = HTTPServer(("127.0.0.1", ctx.options.control_port), _handler(self))
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        ctx.log.info(f"egress control on {self._server.server_address[1]}")

    def response(self, flow: http.HTTPFlow) -> None:
        self._remember(flow)

    def error(self, flow: http.HTTPFlow) -> None:
        # A killed or failed flow is a fact worth having: in replay, a request
        # nobody recorded is exactly what we want a scenario to be able to see.
        self._remember(flow)

    # --- what the suite reads --------------------------------------------

    def calls(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._calls)

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()

    def _remember(self, flow: http.HTTPFlow) -> None:
        request = flow.request
        response = flow.response
        call = {
            "host": request.pretty_host,
            "method": request.method,
            "path": request.path,
            "url": request.pretty_url,
            # Header names lowercased: a scenario asking whether the caller's
            # own credential went out should not have to guess the casing a
            # client happened to use.
            "request_headers": {k.lower(): v for k, v in request.headers.items()},
            "request_body": _text(request.get_content()),
            "status": response.status_code if response else None,
            "response_body": _text(response.get_content()) if response else None,
            "failed": flow.error.msg if flow.error else None,
        }
        with self._lock:
            self._calls.append(call)
            if len(self._calls) > KEEP:
                del self._calls[: len(self._calls) - KEEP]


def _text(content: bytes | None) -> str:
    if not content:
        return ""
    return content.decode("utf-8", errors="replace")


def _handler(recorder: Recorder):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — http.server's spelling
            self._answer(recorder.calls())

        def do_POST(self):  # noqa: N802
            recorder.reset()
            self._answer({"reset": True})

        def _answer(self, payload) -> None:
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            return

    return Handler


addons = [Recorder()]
