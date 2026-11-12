"""The words a refusal uses for the provider an address already signs in with."""

import pytest

from app.modules.identity.infrastructure.supertokens_auth.auth_method_conflicts import (
    get_thirdparty_conflict_reason,
)


@pytest.mark.parametrize(
    ("third_party_id", "name"),
    [("google", "Google"), ("active-directory", "Microsoft"), ("apple", "Apple")],
)
def test_names_each_registered_provider_as_people_know_it(
    third_party_id: str, name: str
) -> None:
    assert get_thirdparty_conflict_reason(third_party_id) == (
        f"This email is already registered with {name}. Please sign in using {name}."
    )
