"""An edit must not destroy what the form could not carry.

Applying a submitted config was a wholesale replace, and a GET-edit-PATCH round
trip -- which is what the UI does -- broke on it twice.

Secrets come back from the API masked, so re-submitting wrote the literal
`********` over the real value. And keys the system wrote are not in the user's
form at all: MCP OAuth registration stores an `oauth` block after validation
and only at create time, so a replace dropped it and nothing re-negotiates,
leaving an install that had been signed into unable to refresh anyone.
"""

from __future__ import annotations

from app.modules.connectors.services.install_update import merged_install_config

MASK = "********"


def test_a_resubmitted_mask_does_not_overwrite_the_real_secret():
    merged = merged_install_config(
        {"oauth2_credentials": {"client_id": "cid", "client_secret": "real"}},
        {"oauth2_credentials": {"client_id": "cid", "client_secret": MASK}},
    )
    assert merged["oauth2_credentials"]["client_secret"] == "real"


def test_a_mask_inside_a_free_form_header_map_is_also_honoured():
    """`extra_headers` is where a tenant puts an API key, so the redactor masks
    it -- and the replace wrote asterisks back as the header value."""
    merged = merged_install_config(
        {"extra_headers": {"Authorization": "Bearer real", "X-Trace": "on"}},
        {"extra_headers": {"Authorization": MASK, "X-Trace": "on"}},
    )
    assert merged["extra_headers"] == {"Authorization": "Bearer real", "X-Trace": "on"}


def test_the_negotiated_oauth_block_survives_an_edit_that_never_mentions_it():
    """It is written after validation and only on create, and the install
    schema rejects it as an unexpected property -- so the form can neither show
    it nor send it back."""
    merged = merged_install_config(
        {
            "server_url": "https://x/mcp",
            "oauth": {"issuer": "https://idp", "client_id": "cid"},
        },
        {"server_url": "https://x/mcp-v2"},
    )
    assert merged["oauth"] == {"issuer": "https://idp", "client_id": "cid"}
    assert merged["server_url"] == "https://x/mcp-v2", "the actual edit still lands"


def test_a_genuinely_new_secret_replaces_the_old_one():
    """Rotation has to keep working: only the mask is treated as "unchanged"."""
    merged = merged_install_config(
        {"oauth2_credentials": {"client_secret": "old"}},
        {"oauth2_credentials": {"client_secret": "brand-new"}},
    )
    assert merged["oauth2_credentials"]["client_secret"] == "brand-new"


def test_a_mask_with_nothing_stored_behind_it_is_kept_as_written():
    """Nothing to restore, and inventing a None would be worse than passing the
    value through to fail validation."""
    merged = merged_install_config({}, {"client_secret": MASK})
    assert merged["client_secret"] == MASK


def test_a_first_config_on_an_install_that_had_none():
    assert merged_install_config(None, {"server_url": "https://x"}) == {
        "server_url": "https://x"
    }
