"""One handle, one spelling: what makes one person one contact."""

from __future__ import annotations

import pytest

from app.modules.contacts.domain.entities import IdentityKind, normalize_handle

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
