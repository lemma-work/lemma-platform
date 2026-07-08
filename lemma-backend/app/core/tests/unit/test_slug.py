from __future__ import annotations

import pytest

from app.core.helpers.slug import slugify, validate_slug


def test_slugify_removes_special_characters():
    assert slugify("Rahul's Research & Ops!") == "rahul-s-research-ops"


def test_validate_slug_allows_dns_safe_slug():
    assert validate_slug("rahul-s-research-ops") == "rahul-s-research-ops"


@pytest.mark.parametrize(
    "value",
    [
        "Rahul Org",
        "rahul's-org",
        "-rahul-org",
        "rahul--org",
        "rahul-org-",
    ],
)
def test_validate_slug_rejects_invalid_slug(value: str):
    with pytest.raises(ValueError):
        validate_slug(value)
