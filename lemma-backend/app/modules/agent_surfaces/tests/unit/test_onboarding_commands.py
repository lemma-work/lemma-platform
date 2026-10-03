"""The three words signup answers to, however they are typed.

A step that knew only the exact lowercase word read every other spelling as an
answer to its question: "Cancel." became a wrong code, "new code please" an
email address that is not one. Each of those is somebody trying to get out of,
or unstuck in, a flow they cannot see the shape of.
"""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.services.onboarding_inputs import command_of


@pytest.mark.parametrize(
    ("text", "command"),
    [
        ("cancel", "cancel"),
        ("Cancel.", "cancel"),
        ("  CANCEL!  ", "cancel"),
        ("stop", "cancel"),
        ("Quit", "cancel"),
        ("resend", "resend"),
        ("Resend code", "resend"),
        ("new code please", "resend"),
        ("send again", "resend"),
        ("change email", "change email"),
        ("Change my email", "change email"),
        ("different email", "change email"),
        ("wrong email!", "change email"),
    ],
)
def test_every_spelling_reads_as_its_command(text: str, command: str) -> None:
    assert command_of(text) == command


@pytest.mark.parametrize(
    "text",
    ["123456", "ada@example.com", "cancel my subscription", "", None, "stop it now"],
)
def test_ordinary_answers_are_not_commands(text: str | None) -> None:
    """A command is the whole message, never a word somewhere inside one."""
    assert command_of(text) is None
