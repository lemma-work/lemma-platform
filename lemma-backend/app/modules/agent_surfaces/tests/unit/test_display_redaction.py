"""Credentials never reach external chat text, whatever they are nested in.

The redactor is pure, so the shape tests call it directly; the journey tests
send a paused approval through the real ``SurfaceEgress`` and read what the
platform was handed, which is where a leak becomes irreversible.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent_surfaces.domain.display_redaction import (
    is_sensitive_key,
    redact_secrets_in_text,
    redact_structure,
)
from app.modules.agent_surfaces.services.approval_preview import (
    approval_action_summary,
)
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    _ask_user_link,
    _delivering_adapter,
    _pending,
    _slack_event,
    _slack_surface,
    build_egress,
    conversation_operations,  # noqa: F401  (autouse fixture)
)

# Assembled from pieces: whole, a fixture like this is shaped like a real
# credential and a secret scanner cannot tell a redaction test from a leak.
SECRET = "not-a" + "-real-" + "value" + "-9f3"


def _shown(args: object) -> str:
    summary = approval_action_summary("exec_command", args)
    assert summary is not None
    return summary


@pytest.mark.parametrize(
    "args",
    [
        {"request": {"password": SECRET}},
        {"request": {"auth": {"user": "a", "passwd": SECRET}}},
        {"items": [{"name": "x", "api_key": SECRET}]},
        {"items": [[{"client_secret": SECRET}]]},
        {"payload": json.dumps({"db": {"password": SECRET}})},
        {"payload": json.dumps([{"credentials": {"pw": SECRET}}])},
        {"headers": {"X-Custom-Auth": SECRET, "Cookie": SECRET}},
        {"settings": {"accessToken": SECRET}},
        {"settings": {"sessionId": SECRET}},
        {"cmd": 'curl -d \'{"password":"' + SECRET + "\"}' https://x.test"},
        {"cmd": 'curl -d "{\\"request\\":{\\"password\\":\\"' + SECRET + '\\"}}" x'},
        {"cmd": "DB_PASSWORD=" + SECRET + " psql -h db"},
        {"cmd": "export API_KEY='" + SECRET + "' && run"},
        {"cmd": "tool --password " + SECRET + " --verbose"},
        {"cmd": "tool --password=" + SECRET},
        {"cmd": "tool --api-key " + SECRET},
        {"cmd": "mysql -u root -p" + SECRET + " app"},
        {"cmd": "sshpass -p " + SECRET + " ssh host"},
        # Pieced together: written whole, a scanner reads this line as a real one.
        {"cmd": "curl " + "-" + "u " + "admin" + ":" + SECRET + " https://x.test"},
        {"cmd": "curl -H 'Authorization: Bearer " + SECRET + "' https://x.test"},
        {"cmd": "curl -H 'authorization: token " + SECRET + "' https://x.test"},
        {"cmd": "psql postgres://admin:" + SECRET + "@db.test/app"},
        {"cmd": "curl https://x.test/data?token=" + SECRET + "&page=2"},
        {"cmd": "echo aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vW1xY2z"},
        {"note": "sk" + "-" + "ab12CD34ef56GH78ij90"},
        {"note": "gh" + "p_" + "ab12CD34ef56GH78ij90KL12"},
        {"note": "xo" + "xb-" + "1234567890-abcdefghij"},
    ],
)
def test_no_credential_shape_survives_into_the_preview(args):
    shown = _shown(args)

    assert SECRET not in shown
    assert "aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vW1xY2z" not in shown
    assert "ab12CD34ef56GH78ij90" not in shown
    assert "1234567890-abcdefghij" not in shown
    assert "***" in shown
    assert "\n" not in shown and "`" not in shown
    assert len(shown) < 300


def test_a_secret_under_a_sensitive_key_hides_the_whole_nested_object():
    assert redact_structure({"token": {"a": 1, "b": [SECRET]}}) == {"token": "***"}


def test_a_secret_past_the_depth_limit_is_dropped_not_shown():
    deep: object = {"password": SECRET}
    for _ in range(12):
        deep = {"n": deep}

    assert SECRET not in json.dumps(redact_structure(deep))


def test_a_secret_is_masked_before_the_preview_is_cut_to_length():
    # The secret starts inside the visible window and would be cut in half by a
    # redact-after-truncate; the head is what a half-secret regex misses.
    padding = "x" * 60
    shown = _shown({"cmd": padding + " --token " + SECRET})

    assert SECRET[:8] not in shown


def test_ordinary_arguments_survive_redaction():
    shown = _shown(
        {
            "table_id": "6f1c2c1e-1111-4222-8333-444455556666",
            "cmd": "mkdir -p /tmp/out && ls -p 8080",
            "note": "shipping the spinner to origin",
        }
    )

    assert "table_id=6f1c2c1e-1111-4222-8333-444455556666" in shown
    assert "mkdir -p /tmp/out" in shown
    assert "spinner" in shown and "origin" in shown


@pytest.mark.parametrize("key", ["password", "apiKey", "X-Auth-Token", "OTP", "pin"])
def test_credential_looking_keys_are_recognised(key):
    assert is_sensitive_key(key)


@pytest.mark.parametrize("key", ["spinner", "origin", "table_id", "title", "mapping"])
def test_ordinary_keys_are_not_mistaken_for_credentials(key):
    assert not is_sensitive_key(key)


def test_a_private_key_block_is_masked_across_lines():
    block = "-----BEGIN RSA PRIVATE KEY-----\nabc\ndef\n-----END RSA PRIVATE KEY-----"

    assert "abc" not in redact_secrets_in_text("run with " + block)


# --- the journey: a paused approval reaches a person's chat --------------------

_LEAKY_ARGS = {
    "tool_name": "exec_command",
    "title": "Run the deploy with token=" + SECRET,
    "reason": "Needs the header 'Authorization: Bearer " + SECRET + "'",
    "args": {"request": {"password": SECRET, "cmd": "deploy"}},
}


async def _paused_approval_egress(adapter):
    surface = _slack_surface()
    conversation_id = uuid4()
    link = await _ask_user_link(surface, conversation_id, _slack_event())
    egress = build_egress(adapter=adapter, surfaces=[surface], existing_link=link)
    egress.delivery.conversation_link_repository.get_by_conversation_id.return_value = (
        link
    )
    agent_conversations.pending_approval.return_value = _pending(
        "request_approval", tool_call_id="tool-9", tool_args=_LEAKY_ARGS
    )
    return egress, conversation_id


async def test_the_native_approval_card_carries_no_nested_credential():
    adapter = _delivering_adapter()
    adapter._render_decision.return_value = True
    egress, conversation_id = await _paused_approval_egress(adapter)

    await egress.send_approval_prompt_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-9"
    )

    plan = adapter._render_decision.await_args.kwargs["approval_plan"]
    everything = json.dumps(plan.model_dump(mode="json"), default=str)
    assert SECRET not in everything
    assert "password" in plan.action_summary


async def test_the_text_approval_prompt_carries_no_nested_credential():
    adapter = _delivering_adapter()
    egress, conversation_id = await _paused_approval_egress(adapter)

    sent = await egress.send_prompt_as_text_for_conversation(
        conversation_id=conversation_id, kind="request_approval", tool_call_id="tool-9"
    )

    assert sent
    assert SECRET not in adapter.send_message.await_args.kwargs["message"]
