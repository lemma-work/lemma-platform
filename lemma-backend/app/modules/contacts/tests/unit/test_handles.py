"""One handle, one spelling: what makes one person one contact."""

from __future__ import annotations

import pytest

from app.modules.contacts.domain.entities import (
    IdentityKind,
    is_stop_request,
    normalize_handle,
)

pytestmark = pytest.mark.unit


def test_a_phone_number_keeps_its_digits_only():
    assert normalize_handle(IdentityKind.PHONE, "+44 7700 900-123") == "447700900123"
    assert normalize_handle(IdentityKind.PHONE, "447700900123") == "447700900123"


def test_an_email_address_is_case_folded():
    assert (
        normalize_handle(IdentityKind.EMAIL, " Dana@Client.Example ")
        == "dana@client.example"
    )


def test_a_telegram_id_is_kept_as_sent():
    assert normalize_handle(IdentityKind.TELEGRAM, " 900100 ") == "900100"


def test_a_number_of_punctuation_alone_normalises_to_nothing():
    assert normalize_handle(IdentityKind.PHONE, "+ ( ) -") == ""


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
