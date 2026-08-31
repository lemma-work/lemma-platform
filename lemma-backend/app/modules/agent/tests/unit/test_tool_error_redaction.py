"""A tool's error text is the one surface that writes into both contexts at once.

It goes into the model's context and into the durable conversation transcript a
person reads back. Logs, telemetry, API exception handlers, connector errors and
function runtime logs all run their free text through `app/core/redaction.py`.
Nothing under `app/modules/agent/` did.
"""

from __future__ import annotations

import pytest

from app.modules.agent.tools.tool_errors import (
    format_tool_error,
    safe_described_error,
    safe_error_text,
)

pytestmark = pytest.mark.unit


class TestSecretsDoNotReachTheTranscript:
    def test_a_signed_url_loses_its_query(self) -> None:
        """`httpx.HTTPStatusError` stringifies with the full URL, and a signed
        storage link carries its credential in the query string."""
        exc = RuntimeError(
            "Server error '500' for url "
            "'https://store.example.com/f.pdf?X-Amz-Signature=deadbeefcafe&x=1'"
        )

        assert "deadbeefcafe" not in safe_error_text(exc)

    def test_a_bearer_token_is_stripped(self) -> None:
        exc = RuntimeError("upstream rejected: Authorization: Bearer sk-live-abc123xyz")

        text = safe_error_text(exc)

        assert "sk-live-abc123xyz" not in text

    def test_the_result_handed_to_the_model_is_redacted(self) -> None:
        """`format_tool_error` is the uniform tool contract -- the shape both the
        frontend and the model read for every failure."""
        exc = RuntimeError("auth failed with token ghp_abcdefghijklmnopqrstuvwxyz0123")

        result = format_tool_error("send_email", exc)

        assert "ghp_abcdefghijklmnopqrstuvwxyz0123" not in str(result)
        assert result["success"] is False

    def test_the_cause_chain_is_redacted_not_dropped(self) -> None:
        """For a wrapped transport failure the cause names the host, port or
        timeout involved -- worth keeping, which is why it needs redacting."""
        try:
            try:
                raise ConnectionError(
                    "connect to https://api.example.com/v1?api_key=sk-secret-value"
                )
            except ConnectionError as inner:
                raise RuntimeError("workspace command failed") from inner
        except RuntimeError as exc:
            text = safe_described_error(exc)

        assert "sk-secret-value" not in text
        assert "workspace command failed" in text

    def test_an_ordinary_error_survives_intact(self) -> None:
        """Redaction must not make failures unreadable -- an agent that cannot
        tell what went wrong cannot adapt to it."""
        exc = FileNotFoundError("No such file or directory: 'video/scenes.py'")

        assert "video/scenes.py" in safe_error_text(exc)

    def test_an_empty_error_still_names_its_type(self) -> None:
        assert safe_error_text(ValueError()) == "ValueError"
