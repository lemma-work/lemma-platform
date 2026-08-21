from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# NOTE: `requests` is imported lazily inside the two functions that use it.
# It is a heavy import (~1s under gVisor) and only the interactive auth/login
# flow needs it; importing it at module top taxed every SDK/CLI invocation.
# Same idea for http.server, threading, webbrowser, secrets: only the
# interactive login flow needs them, but this module loads on every CLI start
# (the CLI imports refresh_cli_session for its 401-retry path).


class LoginTimeoutError(TimeoutError):
    pass


@dataclass
class LoginFlowResult:
    session: dict[str, Any]
    login_url: str
    browser_opened: bool


def fetch_cli_auth_info(
    *,
    base_url: str,
    verify_ssl: bool,
    timeout: float,
) -> dict[str, Any]:
    import requests

    try:
        response = requests.get(
            f"{base_url.rstrip('/')}/auth/cli/info",
            timeout=timeout,
            verify=verify_ssl,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ValueError(
            f"Unable to load CLI auth info from {base_url}: {exc}"
        ) from exc
    return response.json()


def refresh_cli_session(
    *,
    base_url: str,
    refresh_token: str,
    verify_ssl: bool,
    timeout: float,
) -> dict[str, Any]:
    import requests

    try:
        response = requests.post(
            f"{base_url.rstrip('/')}/auth/cli/refresh",
            json={"refresh_token": refresh_token},
            timeout=timeout,
            verify=verify_ssl,
            headers={"Accept": "application/json"},
        )
    except requests.RequestException as exc:
        raise ValueError(
            f"Unable to reach {base_url} for session refresh: {exc}"
        ) from exc
    if response.status_code >= 400:
        try:
            payload = response.json()
        except ValueError:
            payload = {"message": response.text.strip() or response.reason}
        message = (
            payload.get("message")
            or payload.get("detail")
            or "Unable to refresh session"
        )
        raise ValueError(message)
    return response.json()


class _LoopbackCallbackServer:
    def __init__(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self._payload: dict[str, Any] | None = None
        self._event = threading.Event()

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return

            def _cors(self) -> None:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")

            def do_OPTIONS(self) -> None:
                self.send_response(204)
                self._cors()
                self.end_headers()

            def do_POST(self) -> None:
                if self.path != "/callback":
                    self.send_response(404)
                    self._cors()
                    self.end_headers()
                    return

                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    self.send_response(400)
                    self._cors()
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ok":false,"message":"Invalid JSON"}')
                    return

                if not isinstance(payload, dict):
                    self.send_response(400)
                    self._cors()
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ok":false,"message":"Invalid payload"}')
                    return

                outer._payload = payload
                outer._event.set()

                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.callback_url = (
            f"http://127.0.0.1:{self._server.server_address[1]}/callback"
        )
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "_LoopbackCallbackServer":
        self._thread.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def wait_for_payload(self, timeout_seconds: float) -> dict[str, Any]:
        if not self._event.wait(timeout_seconds):
            raise LoginTimeoutError(
                f"Timed out waiting for browser login after {int(timeout_seconds)} seconds."
            )
        if self._payload is None:
            raise LoginTimeoutError("No login payload received.")
        return self._payload

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def run_login_flow(
    *,
    base_url: str,
    verify_ssl: bool,
    timeout: float,
    auth_url: str | None = None,
) -> LoginFlowResult:
    import secrets
    import time
    import webbrowser
    from urllib.parse import urlencode

    info = fetch_cli_auth_info(
        base_url=base_url, verify_ssl=verify_ssl, timeout=timeout
    )
    resolved_auth_url = (auth_url or info.get("auth_frontend_url") or "").rstrip("/")
    if not resolved_auth_url:
        raise ValueError("Auth frontend URL is not configured by the backend.")

    state = secrets.token_urlsafe(24)
    with _LoopbackCallbackServer() as callback_server:
        login_url = f"{resolved_auth_url}/cli/login?" + urlencode(
            {
                "callback": callback_server.callback_url,
                "state": state,
            }
        )

        browser_opened = webbrowser.open(login_url)
        payload = callback_server.wait_for_payload(timeout)

    if payload.get("state") != state:
        raise ValueError("Login callback state mismatch.")

    session = payload.get("session")
    if not isinstance(session, dict):
        raise ValueError("Login callback did not include a session payload.")

    session.setdefault("base_url", base_url.rstrip("/"))
    session.setdefault("obtained_at", int(time.time()))

    return LoginFlowResult(
        session=session,
        login_url=login_url,
        browser_opened=browser_opened,
    )
