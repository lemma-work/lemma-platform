"""Reading what Lemma said to a platform, out of the proxy.

The loopback stand-ins recorded outbound calls in memory and handed scenarios a
tidy view of them. The proxy records the same calls — from the *real* platform
now — and this is that view, rebuilt on top of it.

Only the reading half moved. The normalisation below is a faithful port of what
the old loopback recorder did, because it is not incidental: it encodes which
envelope the product happens to use, and a scenario should assert on what was
said rather than on which shape the adapter chose that day.

What is gone is the answering half. There is no longer anything pretending to be
Telegram; there is a real Telegram, and this reads the conversation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

JSON = dict[str, Any]

#: The methods that put words in front of a person. `getMe`, `setWebhook` and
#: friends are Lemma talking to the platform about itself, and a scenario asking
#: "what did the agent reply" does not mean those.
TELEGRAM_SAYS = ("sendMessage", "sendRichMessage", "sendPhoto", "sendDocument")


@dataclass(frozen=True, slots=True)
class Said:
    """One thing Lemma sent to a platform."""

    method: str
    payload: JSON

    @property
    def chat_id(self) -> str:
        return str(self.payload.get("chat_id", ""))

    @property
    def native_choices(self) -> list[str]:
        """Labels of any native buttons offered with the message.

        The product promises native controls where a platform supports them
        (PS-SURF-021), so a scenario needs to see them rather than only the text.
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
        `message.markdown`. Asserting on one alone quietly misses the other,
        which is a scenario that passes until the adapter picks the other
        envelope and then fails for a reason nobody can see.
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


def telegram_said(egress, *, to_chat: str | int | None = None) -> list[Said]:
    """What Lemma sent to Telegram, in order, oldest first."""
    said: list[Said] = []
    for call in egress.calls_to("telegram.org"):
        method = call.path.rsplit("/", 1)[-1]
        if method not in TELEGRAM_SAYS:
            continue
        payload = call.json_body()
        if not isinstance(payload, dict):
            continue
        message = Said(method=method, payload=payload)
        if to_chat is not None and message.chat_id != str(to_chat):
            continue
        said.append(message)
    return said


def telegram_webhook(egress) -> tuple[str, str]:
    """Where Lemma told Telegram to deliver, and the secret it chose.

    A scenario delivers to that path itself. That is not a shortcut around
    Telegram — it is the only way a deployment nobody can route to from the
    internet can be sent a message at all, and it is what the stand-in was
    really providing all along. The difference now is that the URL and the
    secret come from the call Lemma actually made to the real Telegram, rather
    than from something we wrote that agreed with itself.
    """
    registered = [
        call
        for call in egress.calls_to("telegram.org")
        if call.path.rsplit("/", 1)[-1] == "setWebhook"
    ]
    if not registered:
        raise AssertionError(
            "Lemma never registered a webhook with Telegram, so there is "
            "nowhere to deliver to. The surface did not connect — check the "
            "proxy log, and that the bot token is one Telegram accepts."
        )
    body = registered[-1].json_body() or {}
    url = str(body.get("url", ""))
    secret = str(body.get("secret_token", ""))
    if not url or not secret:
        raise AssertionError(
            f"the webhook Lemma registered carries no {'url' if not url else 'secret'}: "
            f"{json.dumps(body)[:400]}"
        )
    parsed = urlparse(url)
    return (parsed.path + (f"?{parsed.query}" if parsed.query else ""), secret)
