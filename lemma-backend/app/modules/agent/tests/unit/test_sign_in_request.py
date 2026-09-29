"""The agent's "please sign in to this site" message, as a person is asked about it."""

from __future__ import annotations

from uuid import UUID

from app.modules.agent.contracts.sign_in import (
    SignInRequest,
    sign_in_link,
    sign_in_message,
    sign_in_request_from_tool_args,
)

_CONVERSATION = UUID("11111111-1111-1111-1111-111111111111")


def test_the_request_names_the_site_and_reason() -> None:
    assert sign_in_request_from_tool_args(
        {"origin": "https://bank.example", "reason": "download the statement"}
    ) == SignInRequest(origin="https://bank.example", reason="download the statement")


def test_a_reason_is_optional_but_a_site_is_not() -> None:
    assert sign_in_request_from_tool_args({"origin": "https://x.example"}) == (
        SignInRequest(origin="https://x.example", reason="")
    )
    assert sign_in_request_from_tool_args({"reason": "why"}) is None
    assert sign_in_request_from_tool_args({"origin": ""}) is None
    assert sign_in_request_from_tool_args("junk") is None
    assert sign_in_request_from_tool_args(None) is None


def test_the_link_is_keyed_on_the_conversation_and_the_waiting_tool_call() -> None:
    assert sign_in_link(
        "https://app.example/", conversation_id=_CONVERSATION, tool_call_id="call-1"
    ) == (f"https://app.example/sign-in-to-site/{_CONVERSATION}/call-1")


def test_the_message_says_what_and_where_and_that_the_password_is_private() -> None:
    body = sign_in_message(
        origin="https://bank.example",
        reason="download the statement",
        link="https://app.example/sign-in-to-site/c/t",
    )
    assert body.splitlines() == [
        "I need you to sign in to https://bank.example so I can carry on.",
        "What I am doing: download the statement",
        "https://app.example/sign-in-to-site/c/t",
        (
            "The link opens the site in my browser for you. I will not ask for "
            "your password and I cannot see it."
        ),
    ]


def test_without_a_reason_the_line_is_left_out() -> None:
    body = sign_in_message(origin="https://x.example", reason="", link="https://l")
    assert "What I am doing" not in body
