from __future__ import annotations

import base64

import pytest

from app.modules.web_login.services.totp import (
    DEFAULT_PERIOD_SECONDS,
    InvalidTotpSeed,
    normalize_seed,
    seconds_remaining,
    totp,
)

# RFC 6238 Appendix B uses the ASCII secret "12345678901234567890" with SHA-1.
# Authenticator apps hand out base32, so that is what the vault stores.
RFC_SEED = base64.b32encode(b"12345678901234567890").decode("ascii")


@pytest.mark.parametrize(
    "moment,expected",
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_it_matches_the_rfc_6238_vectors(moment: int, expected: str) -> None:
    """Verified against the specification, not against another implementation."""
    assert totp(RFC_SEED, at=moment, digits=8) == expected


def test_the_six_digit_code_is_the_last_six_of_the_eight() -> None:
    """What a site actually asks for."""
    assert totp(RFC_SEED, at=59) == "287082"


def test_the_code_changes_with_the_window() -> None:
    first = totp(RFC_SEED, at=1111111109)
    later = totp(RFC_SEED, at=1111111109 + DEFAULT_PERIOD_SECONDS)
    assert first != later


def test_the_code_holds_still_inside_one_window() -> None:
    assert totp(RFC_SEED, at=1111111110) == totp(RFC_SEED, at=1111111111)


def test_a_pasted_seed_keeps_its_formatting_out_of_the_maths() -> None:
    """Setup pages group the secret and lower-case it; people copy that."""
    grouped = " ".join(
        RFC_SEED.lower()[index : index + 4] for index in range(0, len(RFC_SEED), 4)
    )
    assert normalize_seed(grouped) == RFC_SEED
    assert totp(grouped, at=59) == totp(RFC_SEED, at=59)


def test_an_unpadded_seed_still_works() -> None:
    """Sites quote base32 without padding as often as with it."""
    assert totp(RFC_SEED.rstrip("="), at=59) == totp(RFC_SEED, at=59)


def test_a_seed_that_is_not_base32_is_refused_loudly() -> None:
    """A silently wrong code fails at the site as a wrong password, which is the
    most confusing way this could break."""
    with pytest.raises(InvalidTotpSeed):
        totp("not base32 !!", at=59)


def test_time_remaining_is_within_the_window() -> None:
    # 1111111110 is exactly on a window boundary, so a whole period is left.
    assert seconds_remaining(at=1111111110) == pytest.approx(30.0)
    assert seconds_remaining(at=1111111115) == pytest.approx(25.0)
    assert 0 < seconds_remaining(at=1111111109) <= DEFAULT_PERIOD_SECONDS
