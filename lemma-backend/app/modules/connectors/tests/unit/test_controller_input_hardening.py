"""Untrusted input that reaches a filesystem path or a response body.

Two CodeQL findings, both in code that takes a value straight off the request:
the skill endpoint builds a filename from the connector id in the URL, and the
OAuth callback echoed the provider's error string back to the caller.
"""

from __future__ import annotations

import pytest

from app.modules.connectors.api.connect_request_controller import _safe_oauth_error
from app.modules.connectors.api.connector_controller import (
    SKILLS_DIR,
    _resolve_skill_file,
)

pytestmark = pytest.mark.unit


class TestSkillFileResolution:
    """The connector id comes from the URL and ends up in a path."""

    @pytest.mark.parametrize(
        "connector_id",
        [
            "../../../../etc/passwd",
            "..%2f..%2fetc%2fpasswd",
            "../secrets",
            "..",
            ".",
            "/etc/passwd",
            "gmail/../../../etc/passwd",
            "gmail\x00.md",
            "GMAIL",  # ids are lowercase slugs
            "gmail;rm -rf /",
            "",
        ],
    )
    def test_anything_that_is_not_a_catalog_slug_is_refused(self, connector_id):
        assert _resolve_skill_file(connector_id, None) is None

    @pytest.mark.parametrize("provider", ["../../etc", "lemma/../..", "'; DROP", ""])
    def test_an_unrecognised_provider_never_reaches_the_filename(self, provider):
        # Falls back to the generic doc rather than composing a path from it.
        resolved = _resolve_skill_file("definitely-not-a-real-connector", provider)
        assert resolved is None

    def test_a_resolved_file_always_lives_under_the_skills_directory(self):
        # Whatever a real catalog id resolves to, it must be contained. Checked
        # over the actual shipped docs rather than a synthetic case.
        for path in SKILLS_DIR.glob("*.md"):
            connector_id = path.name.split(".")[0]
            resolved = _resolve_skill_file(connector_id, None)
            if resolved is not None:
                assert SKILLS_DIR in resolved.parents

    def test_a_real_connector_id_still_resolves(self):
        # The guard must not have broken the feature it is guarding.
        existing = sorted(SKILLS_DIR.glob("*.md"))
        if not existing:
            pytest.skip("No skill docs shipped in this checkout.")
        connector_id = existing[0].name.split(".")[0]
        assert _resolve_skill_file(connector_id, None) is not None

    def test_a_missing_doc_is_absent_rather_than_an_error(self):
        assert _resolve_skill_file("no-such-connector", None) is None


class TestOAuthErrorReflection:
    """The provider controls this string, and it reached the response body."""

    @pytest.mark.parametrize(
        "error",
        ["access_denied", "invalid_scope", "server_error", "temporarily_unavailable"],
    )
    def test_real_oauth_error_codes_are_passed_through(self, error):
        # Genuinely useful for the user, so it must survive.
        assert _safe_oauth_error(error) == error

    @pytest.mark.parametrize(
        "error",
        [
            "<script>alert(1)</script>",
            "\"><img src=x onerror=alert(1)>",
            "javascript:alert(1)",
            "a" * 500,
            "error with spaces",
            "",
            "   ",
        ],
    )
    def test_anything_that_is_not_an_error_code_is_replaced(self, error):
        assert _safe_oauth_error(error) == "unrecognized_error"

    def test_the_replacement_carries_no_caller_input(self):
        assert "script" not in _safe_oauth_error("<script>x</script>")
