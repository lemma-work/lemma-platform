"""Runs inside mitmproxy, and is the only thing in this suite that may import it.

The suite talks to this over HTTP rather than importing mitmproxy itself, for two
reasons. The scenario suite keeps a deliberately thin dependency list — `httpx`
and little else — and mitmproxy is a large tree with its own pinned
`cryptography`. And the proxy has to be a *process* the stack is configured to
use, not a library the test imports: the suite's contract is that nothing is
patched, and a proxy the product opted into via `HTTPS_PROXY` is the same kind
of seam as the database it was pointed at.

What it does: remember every request that went out and what came back, answer
questions about them on a control port, and — in `fake` mode — serve the far end
of Telegram and of a third-party API itself.

    GET  /calls            everything, newest last
    POST /reset            forget it all — one scenario's traffic is not another's

Serving the fakes here is what lets the product talk to `api.telegram.org` and
`provider.scenarios.example` for real while nothing leaves the machine. The
alternative the suite used to run — servers on loopback, with the product
pointed at them — meant turning the SSRF guard off for every scenario, because
a connector aimed at 127.0.0.1 is exactly what that guard exists to refuse.
"""

from __future__ import annotations

import json
import pathlib
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from mitmproxy import ctx, http

#: Bounded, because a long run against a chatty provider would otherwise grow
#: without limit inside a process nobody is watching. Ten thousand calls is far
#: more than any scenario makes and far less than a memory problem.
KEEP = 10_000

#: The hostnames the proxy answers for. Real names, deliberately: the product
#: resolves and connects to these as it would in production, and the guard sees
#: a public (or simply unresolvable) host rather than loopback.
TELEGRAM_HOST = "telegram.org"
PROVIDER_HOST = "provider.scenarios.example"


def _load_fake_upstreams():
    """Import the fakes by path, because this runs inside mitmproxy's Python.

    mitmdump is its own interpreter with its own site-packages: `harness` is
    not on its path and putting it there would mean shipping the suite's venv
    into a tool. The module sits next to this file and imports nothing but the
    standard library, precisely so it can be loaded this way.
    """
    import importlib.util

    here = pathlib.Path(__file__).resolve().parent / "fake_upstreams.py"
    spec = importlib.util.spec_from_file_location("_fake_upstreams", here)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable
        raise RuntimeError(f"could not load the fakes from {here}")
    module = importlib.util.module_from_spec(spec)
    # Registered before it is executed: the module uses postponed annotations,
    # and `dataclasses` resolves those by looking its own module up in
    # `sys.modules`. Left unregistered, every `@dataclass` in it fails on a
    # `None` module — which surfaces, unhelpfully, as a connection reset.
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Recorder:
    """Everything Lemma sent out, and what came back."""

    def __init__(self) -> None:
        self._calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._server: HTTPServer | None = None
        self._telegram: Any = None
        self._provider: Any = None

    # --- mitmproxy hooks --------------------------------------------------

    def load(self, loader) -> None:
        loader.add_option(
            name="control_port",
            typespec=int,
            default=0,
            help="Port the suite asks about recorded calls on.",
        )
        loader.add_option(
            name="serve_fakes",
            typespec=bool,
            default=False,
            help="Answer for Telegram and the provider instead of the internet.",
        )

    def running(self) -> None:
        if self._server is not None:
            return
        self._server = HTTPServer(("127.0.0.1", ctx.options.control_port), _handler(self))
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        ctx.log.info(f"egress control on {self._server.server_address[1]}")
        if ctx.options.serve_fakes:
            # Started here rather than by the stack so there is no second
            # process to coordinate, and threaded so a handler that sleeps for
            # thirty seconds — which one of them does, on purpose — blocks a
            # worker thread rather than mitmproxy's event loop.
            fake_upstreams = _load_fake_upstreams()

            self._telegram = fake_upstreams.start_fake_telegram()
            self._provider = fake_upstreams.start_fake_provider()
            ctx.log.info("egress serving the Telegram and provider fakes")

    async def request(self, flow: http.HTTPFlow) -> None:
        """Send the two designated hosts to the fakes, and record the real name.

        Order matters here, and cost an afternoon to find: setting
        `host_header` *before* rewriting `host` does nothing, because changing
        `flow.request.host` re-derives the Host header from it. The public name
        has to be put back afterwards, or the provider's own spec comes back
        self-describing as 127.0.0.1.

        This also needs `connection_strategy=lazy` on the proxy. Eagerly,
        mitmproxy opens the upstream connection before this hook runs, to copy
        the server's TLS certificate — and an unresolvable host fails there,
        long before anything can redirect it.
        """
        host = flow.request.pretty_host
        upstream = None
        if host.endswith(TELEGRAM_HOST):
            upstream = self._telegram
        elif host == PROVIDER_HOST:
            upstream = self._provider
        if upstream is None:
            return
        # Remember what the product actually asked for: the rewrite below would
        # otherwise make every recorded call look like it went to 127.0.0.1,
        # and `calls_to("telegram.org")` would match nothing.
        flow.metadata["egress_host"] = host
        flow.metadata["egress_url"] = flow.request.pretty_url
        flow.request.scheme = "http"
        flow.request.host = "127.0.0.1"
        flow.request.port = upstream.port
        flow.request.host_header = host

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
            # The name the product used, not the one it was redirected to.
            "host": flow.metadata.get("egress_host") or request.pretty_host,
            "method": request.method,
            "path": request.path,
            "url": flow.metadata.get("egress_url") or request.pretty_url,
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
