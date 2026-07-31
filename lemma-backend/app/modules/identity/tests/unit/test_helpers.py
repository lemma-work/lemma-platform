import pytest

from app.core.helpers.humanize import humanize_name
from app.core.helpers.identifiers import normalize_mobile_digits, normalize_telegram

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("abc_def", "Abc Def"),
        ("my-cool-pod", "My Cool Pod"),
        ("sales_2024", "Sales 2024"),
        ("Email-test_pod Pod", "Email Test Pod Pod"),
        ("customer_success-Team Workspace", "Customer Success Team Workspace"),
        ("Acme Support AI", "Acme Support AI"),  # already human -> untouched
        ("Acme Inc", "Acme Inc"),
        ("iOS", "iOS"),  # mixed case preserved
        ("", ""),
        (None, ""),
    ],
)
def test_humanize_name(raw, expected):
    assert humanize_name(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+1 (555) 123-4567", "15551234567"),
        ("5551234567", "5551234567"),
        ("", None),
        (None, None),
        ("abc", None),
    ],
)
def test_normalize_mobile_digits(raw, expected):
    assert normalize_mobile_digits(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("AnukulT", "anukult"),
        ("  Foo  ", "foo"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_telegram(raw, expected):
    assert normalize_telegram(raw) == expected
