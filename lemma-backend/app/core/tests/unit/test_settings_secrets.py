"""Secret settings must not be readable from a repr.

An exception rendered by a web framework, a `structlog` record carrying the
settings object, a debugger frame, `print(settings)` in a shell — every one of
those calls `repr()`, and a `SecretStr` renders as `**********` where a plain
`str` renders the key. That is the whole reason the convention exists, and five
fields were outside it: the encryption key that protects every other stored
secret, both OAuth client secrets, the SMTP password and a search API key.

This asserts the property rather than the annotation, so a field that is
switched back to `str` fails here rather than in an incident.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.core.config import Settings, reveal_secret

# Every field on the core Settings whose value is a credential. Adding one that
# is not here is the case this test cannot catch, so keep it as things are added.
#
# `microsoft_client_secret` and `google_client_secret` are not missing: they left
# with the rest of identity's settings, and the same assertion follows them in
# `identity/tests/unit/test_identity_config.py`. A secret moving class must not
# be a secret losing its cover.
SECRET_FIELDS = (
    "brave_search_api_key",
    "secret_encryption_key",
    "smtp_password",
)


@pytest.mark.parametrize("field", SECRET_FIELDS)
def test_a_secret_setting_is_hidden_in_a_repr(field: str) -> None:
    annotation = Settings.model_fields[field].annotation
    assert "SecretStr" in str(annotation), (
        f"{field} is typed {annotation}, so its value appears in full in any "
        f"repr of the settings object — a traceback, a log record, a debugger"
    )

    plaintext = f"the-real-{field.replace('_', '-')}"
    settings = Settings.model_construct(**{field: SecretStr(plaintext)})

    assert plaintext not in repr(getattr(settings, field))
    assert plaintext not in repr(settings)
    # And still usable at the point of use, which is the other half.
    assert reveal_secret(getattr(settings, field)) == plaintext


def test_reveal_secret_still_takes_a_bare_string() -> None:
    """Tests monkeypatch these with plain strings; that has to keep working."""
    assert reveal_secret("plain") == "plain"
    assert reveal_secret(None) is None
