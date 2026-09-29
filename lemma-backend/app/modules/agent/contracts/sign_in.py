"""Asking a person to sign in to a site for the agent, in the agent's words.

A submodule rather than `contracts/__init__`, like its siblings.
"""

from __future__ import annotations

from app.modules.agent.domain.sign_in_request import (
    SignInRequest,
    sign_in_link,
    sign_in_message,
    sign_in_request_from_tool_args,
)

__all__ = [
    "SignInRequest",
    "sign_in_link",
    "sign_in_message",
    "sign_in_request_from_tool_args",
]
