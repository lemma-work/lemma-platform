"""The context every tool receives carries no model-provider credential.

It used to hold the revealed plaintext key on `runtime_credentials`, a field
nothing read: a secret handed to every tool for no reason, against the rule
that a key is revealed only at its point of use.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.context import ToolContext

pytestmark = pytest.mark.unit


def test_the_tool_context_has_no_field_for_provider_credentials() -> None:
    assert "runtime_credentials" not in ToolContext.model_fields


def test_a_credential_handed_to_the_tool_context_is_not_kept() -> None:
    ctx = ToolContext.model_validate(
        {
            "user_id": uuid4(),
            "pod_id": uuid4(),
            "conversation_id": uuid4(),
            "runtime_credentials": {"api_key": "sk-must-not-survive"},
        }
    )

    assert "sk-must-not-survive" not in repr(ctx.model_dump())
