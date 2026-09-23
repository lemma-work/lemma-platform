"""The buttons a Telegram signup draws belong to the step it is asking about.

One keyboard -- Resend, Change email, Cancel -- used to ride on every reply the
coordinator sent, whatever it had just said. It was correct for one step out of
six. Everywhere else it offered actions the step had no branch for, so pressing
a button the product itself had drawn sent text that fell through to "that is
not an email address".
"""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.domain.onboarding_state import OnboardingStep
from app.modules.agent_surfaces.services.onboarding_replies import (
    telegram_keyboard,
)

pytestmark = pytest.mark.unit


def _labels(step: str | None) -> list[str]:
    markup = telegram_keyboard(step)
    return [
        str(button["text"])
        for row in markup.get("keyboard") or []
        for button in row  # type: ignore[union-attr]
    ]


def test_the_code_step_is_the_one_step_resend_belongs_to() -> None:
    assert _labels(OnboardingStep.AWAITING_CODE) == [
        "Resend",
        "Change email",
        "Cancel",
    ]


def test_the_email_step_offers_nothing_that_needs_a_code_to_exist() -> None:
    """No code has been sent yet, so there is nothing to resend.

    `Change email` is the same kind of nonsense one step earlier: there is no
    address on the row to change away from.
    """
    assert _labels(OnboardingStep.AWAITING_EMAIL) == ["Cancel"]


def test_the_phone_step_asks_for_the_contact_share_and_nothing_else() -> None:
    markup = telegram_keyboard(OnboardingStep.AWAITING_PHONE)
    assert markup["keyboard"] == [
        [{"text": "Share my contact", "request_contact": True}]
    ]


@pytest.mark.parametrize(
    "step",
    [
        OnboardingStep.AWAITING_POD,
        OnboardingStep.CANCELLED,
        OnboardingStep.EXPIRED,
        OnboardingStep.REFUSED,
        OnboardingStep.ORGANIZATION_ACCESS_REQUIRED,
        None,
    ],
)
def test_a_typed_question_or_a_terminal_message_clears_the_keyboard(step) -> None:
    """Clears rather than omits, and the difference is visible to the person.

    Telegram keeps the last custom keyboard on screen until it is told
    otherwise. Sending no `reply_markup` under "Setup cancelled." leaves
    `Resend` sitting there, and the workspace question is answered by typing a
    number or `new <name>` -- neither of which is on any keyboard.
    """
    assert telegram_keyboard(step) == {"remove_keyboard": True}
