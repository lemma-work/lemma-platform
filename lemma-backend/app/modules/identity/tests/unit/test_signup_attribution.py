"""How a person signed up, as their sign-up event says.

``auth.signed_up.method`` is filled in where the path that finished the sign-up
knows it, and left empty where several paths meet. Who created an organization
is held by the identity e2e, which commits one for real.
"""

from __future__ import annotations

from app.modules.identity.domain.events import UserSignedUpEvent
from app.modules.identity.domain.user_entities import UserEntity


def _signed_up(user: UserEntity) -> UserSignedUpEvent:
    (event,) = [e for e in user.collect_events() if isinstance(e, UserSignedUpEvent)]
    return event


def test_a_sign_up_says_how_it_was_made_when_the_path_knows() -> None:
    user = UserEntity(email="test+oauth@example.com")
    user.mark_signed_up(method="google")
    assert _signed_up(user).method == "google"

    coded = UserEntity(email="test+code@example.com", is_verified=False)
    assert coded.mark_email_verified(signup_method="email_code") is True
    assert _signed_up(coded).method == "email_code"


def test_a_sign_up_finished_by_the_verification_link_does_not_guess() -> None:
    """A password sign-up, an OAuth account with an unverified address and a
    code sign-in that takes back a preregistered one all end on that link."""
    user = UserEntity(email="test+link@example.com", is_verified=False)
    user.mark_email_verified()
