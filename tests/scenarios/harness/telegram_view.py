"""What Lemma said to Telegram, read back out of the proxy.

The shape a scenario used to get from `FakeTelegram` — the webhook it
registered, and the messages it sent to a chat — except nothing here runs a
server. The product talked to `api.telegram.org`; the proxy answered and wrote
the exchange down; this reads it.

That is the whole difference, and it is the point: a scenario no longer starts,
points at, or stops anything. It asks what happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from harness.platforms import Said, telegram_said, telegram_webhook


@dataclass(frozen=True, slots=True)
class TelegramView:
    """The Telegram side of a scenario, as the proxy saw it."""

    egress: Any

    @property
    def webhook_path(self) -> str:
        """Where Lemma asked Telegram to deliver, read from its own setWebhook.

        Deliberately not built from a template: a scenario delivering to a path
        it guessed proves the product answers that path, not that the product
        registered it. Reading it back means a change in how Lemma registers
        webhooks surfaces here rather than passing quietly.
        """
        return telegram_webhook(self.egress)[0]

    @property
    def webhook_secret(self) -> str:
        return telegram_webhook(self.egress)[1]

    def messages_to(self, chat_id: int | str) -> list[Said]:
        """Everything the agent said in one chat, oldest first."""
        return telegram_said(self.egress, to_chat=chat_id)

    def clear(self) -> None:
        """Forget the traffic so far — one scenario's calls are not another's."""
        self.egress.forget()
