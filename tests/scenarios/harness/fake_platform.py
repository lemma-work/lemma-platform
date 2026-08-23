"""A stand-in for a messaging platform, so surfaces can be tested end to end.

A surface is the one part of the product whose other end is somebody else's
server. Testing it for real would mean a Telegram bot, a Slack workspace, and a
network — none of which belong in a suite that runs on every change.

This is **not** a mock of Lemma. Lemma runs entirely for real; what stands in is
Telegram, and it stands in the way a self-hosted Bot API server would. The
platform supports that natively: `api_base_url` on a connected account overrides
where the client sends, and the comment on `_TELEGRAM_API_BASE` names
"self-hosted Bot API servers" as the reason it exists. So a scenario points a
surface at this server using a documented product capability, not a patched
constant.

What it gives a scenario:

* `getMe` and `setWebhook`, so connecting a surface succeeds.
* `sendMessage`, recorded — which is how a scenario knows the agent replied,
  and what it said.

Everything else answers `{"ok": true}`, so an adapter calling something new
fails on the assertion it cares about rather than on plumbing.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse

JSON = dict[str, Any]

#: What every attachment this fake serves contains. Small, and recognisable in
#: an assertion — a scenario checking the pod received the file can check it
#: received *this*.
FILE_CONTENTS = b"name,amount\nwidgets,42\n"


@dataclass
class SentMessage:
    """One outbound call the platform received."""

    method: str
    payload: JSON

    @property
    def chat_id(self) -> str:
        return str(self.payload.get("chat_id", ""))

    @property
    def native_choices(self) -> list[str]:
        """Labels of any native buttons offered with the message.

        The product promises native controls where a platform supports them
        (PS-SURF-021), so a scenario needs to see them rather than only the
        text.
        """
        markup = self.payload.get("reply_markup")
        if not isinstance(markup, dict):
            return []
        labels: list[str] = []
        for rows in ("keyboard", "inline_keyboard"):
            for row in markup.get(rows) or []:
                for button in row or []:
                    if isinstance(button, dict) and button.get("text"):
                        labels.append(str(button["text"]))
        return labels

    @property
    def text(self) -> str:
        """The words a person would see, whichever shape carried them.

        `sendMessage` puts them in `text`; `sendRichMessage` nests them under
        `message.markdown`. A scenario should assert on what was said, not on
        which envelope the adapter chose.
        """
        for key in ("text", "caption", "body"):
            value = self.payload.get(key)
            if value:
                return str(value)
        for envelope in ("rich_message", "message"):
            nested = self.payload.get(envelope)
            if isinstance(nested, dict):
                for key in ("markdown", "text", "plain"):
                    value = nested.get(key)
                    if value:
                        return str(value)
        return ""


@dataclass
class FakeTelegram:
    """A running stand-in. Start with :func:`start_fake_telegram`."""

    api_base: str
    bot_username: str
    _server: HTTPServer
    _thread: threading.Thread
    sent: list[SentMessage] = field(default_factory=list)
    _state: dict[str, str] = field(default_factory=dict)

    @property
    def webhook_secret(self) -> str:
        """The secret Lemma registered, which inbound updates must carry."""
        return self._state.get("secret_token", "")

    @property
    def webhook_path(self) -> str:
        """The path Lemma told the platform to deliver to.

        Delivering here rather than to a hand-written path is the point: it
        proves Lemma registered somewhere it actually listens. A scenario that
        guesses the path can pass while real delivery is broken.
        """
        url = self._state.get("webhook_url", "")
        if not url:
            raise AssertionError(
                "no webhook was registered; the surface never connected"
            )
        parsed = urlparse(url)
        return parsed.path + (f"?{parsed.query}" if parsed.query else "")

    #: Anything that puts words in front of a person. Not just `sendMessage`:
    #: the Telegram adapter reaches for `sendRichMessage` when the reply carries
    #: formatting, and a scenario asserting only on `sendMessage` would call a
    #: perfectly good answer a missing one.
    SEND_METHODS = ("sendMessage", "sendRichMessage", "sendPhoto", "sendDocument")

    def messages_to(self, chat_id: str | int) -> list[SentMessage]:
        return [
            message
            for message in self.sent
            if message.method in self.SEND_METHODS
            and message.chat_id == str(chat_id)
        ]

    def clear(self) -> None:
        self.sent.clear()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)



def _only_where_the_deployment_can_call_back() -> None:
    """Every server below binds loopback, so only a local deployment reaches it.

    Checked here rather than at each of the dozen fixtures that start one,
    because the failure it prevents is somebody adding a thirteenth and not
    knowing. Against a deployment these scenarios skip with a reason; the
    stand-ins stay exactly as useful as they always were on a stack the suite
    boots itself.
    """
    from harness.credentials import needs
    from harness.environment import LOOPBACK_REACHABLE

    needs(LOOPBACK_REACHABLE)


def start_fake_telegram(*, bot_username: str = "lemma_scenarios_bot") -> FakeTelegram:
    _only_where_the_deployment_can_call_back()
    recorded: list[SentMessage] = []
    #: Telegram remembers the webhook it was given, and Lemma reads it back to
    #: confirm registration took. A fake that forgets fails that confirmation.
    state: dict[str, str] = {"webhook_url": "", "secret_token": ""}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            """Silence the default stderr access log; it drowns pytest output."""

        def _reply(self, body: JSON) -> None:
            encoded = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _serve_file(self) -> None:
            """The bytes behind an attachment, at Telegram's own download path."""
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(FILE_CONTENTS)))
            self.end_headers()
            self.wfile.write(FILE_CONTENTS)

        def _handle(self) -> None:
            # Downloads live under /file/bot<token>/<path>, not under the method
            # namespace, so they are routed before anything is parsed as a call.
            if "/file/bot" in self.path:
                self._serve_file()
                return
            # Telegram's shape is /bot<token>/<method>.
            method = urlparse(self.path).path.rsplit("/", 1)[-1]
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                payload = json.loads(raw) if raw else {}
            except ValueError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}

            recorded.append(SentMessage(method=method, payload=payload))

            if method == "getFile":
                # Telegram hands back where the bytes live, and the client then
                # fetches them from the /file/bot<token>/ prefix. A fake that
                # answers getFile and serves nothing leaves an attachment
                # "received" and empty, which is the failure this exists to
                # rule out.
                file_id = str(payload.get("file_id") or "file")
                self._reply({
                    "ok": True,
                    "result": {
                        "file_id": file_id,
                        "file_unique_id": file_id,
                        "file_size": len(FILE_CONTENTS),
                        "file_path": f"documents/{file_id}",
                    },
                })
            elif method == "getMe":
                self._reply({
                    "ok": True,
                    "result": {
                        "id": 424242,
                        "is_bot": True,
                        "first_name": "Lemma Scenarios",
                        "username": bot_username,
                    },
                })
            elif method.startswith("send") and "chat_id" in payload:
                self._reply({
                    "ok": True,
                    "result": {
                        "message_id": len(recorded),
                        "chat": {"id": payload.get("chat_id")},
                        "text": payload.get("text", ""),
                    },
                })
            elif method == "setWebhook":
                state["webhook_url"] = str(payload.get("url") or "")
                state["secret_token"] = str(payload.get("secret_token") or "")
                self._reply({"ok": True, "result": True})
            elif method == "deleteWebhook":
                state["webhook_url"] = ""
                state["secret_token"] = ""
                self._reply({"ok": True, "result": True})
            elif method == "getWebhookInfo":
                # Lemma compares this against the URL it just set and fails the
                # connection if they differ, so it has to be the real value.
                self._reply({
                    "ok": True,
                    "result": {
                        "url": state["webhook_url"],
                        "has_custom_certificate": False,
                        "pending_update_count": 0,
                    },
                })
            else:
                # sendChatAction, and anything an adapter adds later.
                self._reply({"ok": True, "result": True})

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
            self._handle()

        def do_GET(self) -> None:  # noqa: N802
            self._handle()

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return FakeTelegram(
        # The client appends `<token>/<method>`, so the base ends at `/bot`.
        api_base=f"http://{host}:{port}/bot",
        bot_username=bot_username,
        _server=server,
        _thread=thread,
        sent=recorded,
        _state=state,
    )


# --- a generic HTTP provider ------------------------------------------------


@dataclass
class ReceivedCall:
    """One request the fake provider received."""

    method: str
    path: str
    body: JSON
    headers: dict[str, str]

    @property
    def authorization(self) -> str:
        return self.headers.get("authorization", "")


@dataclass
class FakeProvider:
    """A third-party HTTP API, so connector operations can execute for real.

    Lemma supports connectors of kind ``http`` configured with a ``server_url``
    and an inline OpenAPI spec. That is a first-class product feature — it is
    how anyone connects an internal API — so pointing one at this server is a
    scenario using the product, not a hole cut for testing.

    Records every call, so a scenario can assert that executing an operation
    actually reached the provider, with the caller's credential on it.
    """

    base_url: str
    _server: HTTPServer
    _thread: threading.Thread
    received: list[ReceivedCall] = field(default_factory=list)

    @property
    def spec_url(self) -> str:
        """Where this server publishes its own OpenAPI description.

        Lemma fetches the spec rather than taking it inline, which is the right
        way round — an API that describes itself stays described as it changes.
        """
        return f"{self.base_url}/openapi.json"

    def calls_to(self, path: str) -> list[ReceivedCall]:
        return [call for call in self.received if call.path == path]

    def clear(self) -> None:
        self.received.clear()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def openapi_spec(self) -> JSON:
        """The spec this server publishes, for a scenario that wants to read it."""
        return _spec_for(self.base_url)


def _spec_for(base_url: str) -> JSON:
    """A two-operation OpenAPI description of the fake provider."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Scenarios Provider", "version": "1.0.0"},
        "servers": [{"url": base_url}],
        # Declared so the connector has an auth scheme to apply. Without one,
        # no credential is sent — correctly — and a scenario asserting the
        # caller's token arrives would be asserting against a spec that never
        # asked for it.
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer"}
            }
        },
        "security": [{"bearerAuth": []}],
        "paths": {
            "/widgets": {
                "get": {
                    "operationId": "listWidgets",
                    "summary": "List widgets",
                    "responses": {
                        "200": {
                            "description": "Widgets",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"}
                                }
                            },
                        }
                    },
                },
                "post": {
                    "operationId": "createWidget",
                    "summary": "Create a widget",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"}
                                }
                            },
                        }
                    },
                },
            },
            "/broken": {
                "get": {
                    "operationId": "brokenOperation",
                    "summary": "An operation the provider cannot serve",
                    "responses": {
                        "200": {
                            "description": "Never happens",
                            "content": {
                                "application/json": {"schema": {"type": "object"}}
                            },
                        }
                    },
                }
            },
            "/slow": {
                "get": {
                    "operationId": "slowOperation",
                    "summary": "An operation that takes far too long",
                    "responses": {
                        "200": {
                            "description": "Eventually",
                            "content": {
                                "application/json": {"schema": {"type": "object"}}
                            },
                        }
                    },
                }
            },
        },
        }


def start_fake_provider() -> FakeProvider:
    _only_where_the_deployment_can_call_back()
    received: list[ReceivedCall] = []
    widgets: list[JSON] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            """Quiet."""

        def _reply(self, status: int, body: JSON) -> None:
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _record(self) -> JSON:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else {}
            except ValueError:
                body = {}
            received.append(
                ReceivedCall(
                    method=self.command,
                    path=urlparse(self.path).path,
                    body=body if isinstance(body, dict) else {},
                    headers={k.lower(): v for k, v in self.headers.items()},
                )
            )
            return received[-1].body

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/openapi.json":
                # Not recorded: fetching the description is Lemma discovering
                # the provider, not a scenario calling an operation.
                self._reply(200, _spec_for(f"http://{self.headers.get('Host')}"))
                return
            self._record()
            if path == "/broken":
                # A provider having a bad day. Lemma has to report this as the
                # provider failing, not as the pod failing.
                self._reply(500, {"error": "the provider is having a bad day"})
                return
            if path == "/slow":
                # Longer than any sensible outbound timeout, so the scenario is
                # about Lemma giving up rather than about this server.
                time.sleep(30)
                self._reply(200, {"widgets": []})
                return
            self._reply(200, {"widgets": widgets})

        def do_POST(self) -> None:  # noqa: N802
            body = self._record()
            widget = {"id": len(widgets) + 1, "name": body.get("name", "")}
            widgets.append(widget)
            self._reply(201, widget)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return FakeProvider(
        base_url=f"http://{host}:{port}",
        _server=server,
        _thread=thread,
        received=received,
    )


# --- email ------------------------------------------------------------------


@dataclass
class SentEmail:
    """One message the platform was asked to send."""

    payload: JSON

    @property
    def to(self) -> list[str]:
        recipients = self.payload.get("to")
        if isinstance(recipients, str):
            return [recipients]
        return [str(item) for item in (recipients or [])]

    @property
    def subject(self) -> str:
        return str(self.payload.get("subject") or "")

    @property
    def body(self) -> str:
        for key in ("text", "html"):
            value = self.payload.get(key)
            if value:
                return str(value)
        return ""

    @property
    def headers(self) -> dict[str, str]:
        """Threading headers, lowercased.

        `In-Reply-To` and `References` are what make a reply land inside the
        conversation a person is already reading, rather than starting a new one
        beside it. They are the whole difference between "behaves like email"
        and "sends email".
        """
        raw = self.payload.get("headers")
        if not isinstance(raw, dict):
            return {}
        return {str(k).lower(): str(v) for k, v in raw.items()}


@dataclass
class FakeResend:
    """Resend's API, so a scenario can read the mail Lemma actually sent.

    Reached the same way the Telegram stand-in is: `api_base_url` on the
    connected account, which the service reads in place of `api.resend.com`.
    """

    api_base: str
    _server: HTTPServer
    _thread: threading.Thread
    sent: list[SentEmail] = field(default_factory=list)

    def to(self, address: str) -> list[SentEmail]:
        return [message for message in self.sent if address in message.to]

    def clear(self) -> None:
        self.sent.clear()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def start_fake_resend() -> FakeResend:
    _only_where_the_deployment_can_call_back()
    recorded: list[SentEmail] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            """Quiet."""

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = {}
            if isinstance(payload, dict):
                recorded.append(SentEmail(payload=payload))
            body = json.dumps({"id": f"email-{len(recorded)}"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return FakeResend(
        api_base=f"http://{host}:{port}",
        _server=server,
        _thread=thread,
        sent=recorded,
    )
