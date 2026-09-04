"""The names an OIDC provider hands us, and the ones it does not."""

from app.modules.identity.infrastructure.supertokens_auth.provider_profile import (
    MAX_NAME_LENGTH,
    names_from_provider,
    split_full_name,
)


def test_prefers_structured_claims_over_the_display_name():
    first, last = names_from_provider(
        {"given_name": "Ada", "family_name": "Lovelace", "name": "Countess Ada"},
        None,
    )
    assert (first, last) == ("Ada", "Lovelace")


def test_falls_back_to_splitting_the_display_name():
    assert names_from_provider({"name": "Ada Lovelace"}, None) == ("Ada", "Lovelace")


def test_keeps_a_multi_word_family_name_whole():
    assert split_full_name("Ada van der Lovelace") == ("Ada", "van der Lovelace")


def test_a_single_word_name_has_no_family_name():
    assert names_from_provider({"name": "Prince"}, None) == ("Prince", None)


def test_prefers_the_signed_id_token_over_the_userinfo_response():
    first, last = names_from_provider(
        {"given_name": "Ada", "family_name": "Lovelace"},
        {"given_name": "Someone", "family_name": "Else"},
    )
    assert (first, last) == ("Ada", "Lovelace")


def test_falls_through_to_userinfo_when_the_id_token_carries_no_name():
    first, last = names_from_provider(
        {"email": "ada@example.com"},
        {"given_name": "Ada", "family_name": "Lovelace"},
    )
    assert (first, last) == ("Ada", "Lovelace")


def test_a_given_name_alone_is_enough_to_stop_asking():
    assert names_from_provider({"given_name": "Ada"}, None) == ("Ada", None)


def test_returns_nothing_when_the_provider_said_nothing():
    assert names_from_provider(None, None) == (None, None)
    assert names_from_provider({}, {}) == (None, None)
    assert names_from_provider({"name": "   "}, None) == (None, None)


def test_ignores_non_string_claims_rather_than_coercing_them():
    assert names_from_provider({"given_name": 42, "family_name": ["x"]}, None) == (
        None,
        None,
    )


def test_collapses_whitespace_and_bounds_length():
    first, last = names_from_provider({"given_name": "  Ada\n\tGrace  "}, None)
    assert first == "Ada Grace"

    long_first, _ = names_from_provider({"given_name": "A" * 500}, None)
    assert long_first is not None
    assert len(long_first) == MAX_NAME_LENGTH
