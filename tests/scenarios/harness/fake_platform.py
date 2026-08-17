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
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse

JSON = dict[str, Any]


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


def start_fake_telegram(*, bot_username: str = "lemma_scenarios_bot") -> FakeTelegram:
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

        def _handle(self) -> None:
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

            if method == "getMe":
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
