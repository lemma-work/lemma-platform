"""One handle, one spelling: what makes one person one contact."""

from __future__ import annotations

import pytest

from app.modules.contacts.domain.entities import (
    IdentityKind,
    is_start_request,
    is_stop_request,
    normalize_handle,
)
from app.modules.contacts.domain.handles import (
    DEFAULT_HOST_ISSUER,
    handle_hash,
    host_handle,
    host_issuer,
    issuer_of,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "spelled",
    ["+1 (415) 555-0100", "14155550100", "+14155550100", "001 415 555 0100"],
)
def test_a_phone_number_is_e164_however_it_is_spelled(spelled):
    """A member typing it, and WhatsApp naming the sender, are one number."""
    assert normalize_handle(IdentityKind.PHONE, spelled) == "+14155550100"


def test_a_number_without_its_country_code_is_read_in_the_region():
    assert (
        normalize_handle(IdentityKind.PHONE, "(415) 555-0100", region="US")
        == "+14155550100"
    )
    assert (
        normalize_handle(IdentityKind.PHONE, "07700 900123", region="GB")
        == "+447700900123"
    )


def test_an_international_number_is_not_read_as_a_local_one():
    """WhatsApp's digits, with a region set that they do not belong to."""
    assert (
        normalize_handle(IdentityKind.PHONE, "14155550100", region="GB")
        == "+14155550100"
    )
    assert (
        normalize_handle(IdentityKind.PHONE, "447700900123", region="US")
        == "+447700900123"
    )


def test_a_number_the_library_cannot_place_keeps_its_digits():
    """A platform vouched for it, so it still names somebody."""
    assert normalize_handle(IdentityKind.PHONE, "+999 12") == "+99912"


def test_an_email_address_is_case_folded():
    assert (
        normalize_handle(IdentityKind.EMAIL, " Dana@Client.Example ")
        == "dana@client.example"
    )


def test_a_telegram_id_is_kept_as_sent():
    assert normalize_handle(IdentityKind.TELEGRAM, " 900100 ") == "900100"


def test_a_number_of_punctuation_alone_normalises_to_nothing():
    assert normalize_handle(IdentityKind.PHONE, "+ ( ) -") == ""


def test_an_opt_out_is_keyed_by_the_normalised_handle():
    assert handle_hash(IdentityKind.PHONE, "+1 (415) 555-0100") == handle_hash(
        IdentityKind.PHONE, "14155550100"
    )
    assert handle_hash(IdentityKind.EMAIL, "Dana@X.example") == handle_hash(
        IdentityKind.EMAIL, "dana@x.example"
    )
    assert len(handle_hash(IdentityKind.EMAIL, "dana@x.example")) == 32


def test_a_host_identity_names_the_product_that_vouched_for_it():
    assert host_handle("acme-app", "42") == "host:acme-app:42"
    assert issuer_of(IdentityKind.HOST, "host:acme-app:42") == "acme-app"
    assert issuer_of(IdentityKind.EMAIL, "host:acme-app:42") is None
    assert host_issuer(None) == host_issuer(" ") == DEFAULT_HOST_ISSUER
    assert host_issuer(" Acme-App ") == "acme-app"
    with pytest.raises(ValueError):
        host_issuer("acme app")


@pytest.mark.parametrize("text", ["START", "start.", "Unstop", "subscribe"])
def test_a_whole_message_asking_to_start_is_a_start(text):
    assert is_start_request(text)


@pytest.mark.parametrize("text", ["yes", "start the order", ""])
def test_a_message_that_only_mentions_starting_is_not(text):
    assert not is_start_request(text)


@pytest.mark.parametrize(
    "text", ["STOP", "stop", "  Stop!  ", "STOPALL", "unsubscribe.", "Opt out"]
)
def test_a_whole_message_asking_to_stop_is_a_stop(text):
    assert is_stop_request(text)


@pytest.mark.parametrize(
    "text", ["Stop sending the wrong size", "please stop", "", "STOP STOP"]
)
def test_a_message_that_only_mentions_stopping_is_not(text):
    assert not is_stop_request(text)
