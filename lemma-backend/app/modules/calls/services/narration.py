"""What the voice is told about its team and the screen, word for word.

The voice knows only what these notes say. Each states a fact the call has
just established -- a request reached the team, the team is working, it
finished, it failed, this is on the screen -- so the voice never has to guess,
and a guess is how it came to say it was looking something up while nothing
ran.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import JsonValue

from app.modules.calls.domain.frames import decode
from app.modules.calls.domain.voice_brief import CANVAS_NOTE, TEAM_NOTE

_MAX_QUOTED = 300


def passed_on(request: str) -> str:
    return (
        f'{TEAM_NOTE} Passed on to your team: "{request[:_MAX_QUOTED]}". You may '
        "now tell the person it is being looked into."
    )


def team_working(running: bool) -> str:
    if running:
        return f"{TEAM_NOTE} Your team is working on it right now."
    return (
        f"{TEAM_NOTE} Your team has nothing running now. Do not say anything is "
        "still being worked on unless a new note says so."
    )


def team_failed(reason: str) -> str:
    return (
        f"{TEAM_NOTE} Your team's work failed: {reason[:_MAX_QUOTED]} Tell the "
        "person plainly that it did not get done, and why."
    )


def team_finished(answer: str | None) -> str:
    if answer:
        return f"{TEAM_NOTE} Your team finished: {answer[:1500]}"
    return (
        f"{TEAM_NOTE} Your team finished without anything to report. Tell the "
        "person if they are waiting on it."
    )


def refused(reason: str) -> str:
    return (
        f"{TEAM_NOTE} Your team cannot take requests on this call: {reason} "
        "Tell the person this plainly, once. Do not ask them to repeat "
        "themselves, and do not say anything is being looked into."
    )


def not_received() -> str:
    return (
        f"{TEAM_NOTE} Your team did not receive the last request; nothing is "
        "being done about it. Ask the person to say it again."
    )


def describe(args: JsonValue) -> tuple[str, str]:
    """A canvas item's title and the line the voice is told about it.

    The line is the item's `fallback`: the plain-text summary every surface
    that cannot render gets, which a voice is one of.
    """
    args = args if isinstance(args, dict) else {}
    raw = args.get("data")
    # A widget's data may arrive as JSON text; the display decodes it too.
    data = decode(raw) if isinstance(raw, str) else raw
    data = data if isinstance(data, dict) else {}
    path = args.get("path")
    title = (
        args.get("name")
        or data.get("title")
        or (str(path).rsplit("/", 1)[-1] if path else None)
        or str(args.get("type") or "").lower()
    )
    caption = args.get("fallback") or data.get("caption") or ""
    return str(title or "an item"), str(caption)


def now_on_screen(title: str, caption: str) -> str:
    note = f"{CANVAS_NOTE} Now on the person's screen: {title}"
    return note + (f" -- {caption}" if caption else "")


def looking_at(title: str) -> str:
    return f"{CANVAS_NOTE} The person is looking at: {title}"


def on_screen(described: Sequence[tuple[str, str]]) -> str:
    """What a resumed voice is told the person's screen shows."""
    title, caption = described[-1]
    note = f"{CANVAS_NOTE} Back on the call, the person's screen shows: {title}"
    if caption:
        note += f" -- {caption}"
    earlier = [each for each, _ in described[:-1]]
    if earlier:
        note += ". They can page back to: " + "; ".join(earlier[-10:])
    return note + "."
