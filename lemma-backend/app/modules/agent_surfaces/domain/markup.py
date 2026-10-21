"""A platform's markup, read into what a channel line is made of.

Slack writes a mention as ``<@U123>`` and a link as ``<https://…|label>``;
Teams wraps a mention in ``<at>Name</at>`` and says who it is in the
activity's ``entities``; Telegram marks names and links by offset; WhatsApp
writes an ``@`` and a phone number. A line in Lemma has one shape for all of
them -- readable text, plus the people and links it names -- so each is read
once, here, into segments: plain text, somebody named by their platform id,
or a link. Who a platform id is, is the caller's question (it needs the
database); this only reads what the message says.

A message with no markup reads as one text segment, and its text is left
exactly as it arrived.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)


@dataclass(frozen=True, slots=True)
class Named:
    """Somebody the message names, by their id on the platform."""

    external_id: str
    #: What the message called them, where it said.
    label: str | None = None
    #: The platform says this is the bot itself.
    is_bot: bool = False


@dataclass(frozen=True, slots=True)
class Link:
    url: str
    #: The text shown for it, when that is not the address itself.
    label: str | None = None


type Segment = str | Named | Link


@dataclass(frozen=True, slots=True)
class Markup:
    segments: tuple[Segment, ...]

    @property
    def is_plain(self) -> bool:
        return all(isinstance(segment, str) for segment in self.segments)

    @property
    def named(self) -> tuple[Named, ...]:
        return tuple(segment for segment in self.segments if isinstance(segment, Named))


def plain(text: str) -> Markup:
    return Markup(segments=(text,))


def markup_of(parsed: ParsedInboundSurfaceEvent, *, text: str | None = None) -> Markup:
    """The message's text -- or ``text``, what was made of it -- read for the
    platform it came from."""
    text = parsed.message_text if text is None else text
    match parsed.platform:
        case SurfacePlatform.SLACK:
            return slack_markup(text, bot_ids=_slack_bot_ids(parsed.raw_payload))
        case SurfacePlatform.TEAMS:
            return teams_markup(text, activity=parsed.raw_payload)
        case SurfacePlatform.TELEGRAM:
            return telegram_markup(text, update=parsed.raw_payload)
        case SurfacePlatform.WHATSAPP:
            return whatsapp_markup(text)
        case _:
            return plain(text)


# -- Slack ---------------------------------------------------------------------

#: ``<…>``: a mention, a channel, a special word, or a link.
_SLACK_TOKEN = re.compile(r"<([^<>\n]+)>")
#: The broadcast words Slack writes as ``<!here>``.
_SLACK_SPECIAL = {"here": "@here", "channel": "@channel", "everyone": "@everyone"}


def slack_markup(text: str, *, bot_ids: frozenset[str] = frozenset()) -> Markup:
    """Slack's ``mrkdwn`` tokens: ``<@U…>``, ``<#C…|name>``, ``<!here>``,
    ``<url|label>``. Slack escapes ``&``, ``<`` and ``>`` in the rest."""
    segments: list[Segment] = []
    position = 0
    for match in _SLACK_TOKEN.finditer(text):
        segments.append(_slack_unescape(text[position : match.start()]))
        segments.append(_slack_token(match.group(1), bot_ids))
        position = match.end()
    segments.append(_slack_unescape(text[position:]))
    return _joined(segments)


def _slack_token(token: str, bot_ids: frozenset[str]) -> Segment:
    body, _, label = token.partition("|")
    label = _slack_unescape(label).strip()
    if body.startswith("@"):
        user_id = body[1:].strip()
        return Named(
            external_id=user_id,
            label=label.lstrip("@") or None,
            is_bot=user_id in bot_ids,
        )
    if body.startswith("#"):
        return f"#{label or body[1:]}"
    if body.startswith("!"):
        return _slack_special(body[1:], label)
    return _slack_link(_slack_unescape(body).strip(), label)


def _slack_special(word: str, label: str) -> str:
    """``<!here>``, ``<!subteam^S1|@eng>``, ``<!date^…|fallback>``, as read."""
    if word in _SLACK_SPECIAL:
        return _SLACK_SPECIAL[word]
    if word.startswith("subteam^"):
        return label if label.startswith("@") else f"@{label or 'group'}"
    return label


def _slack_link(url: str, label: str) -> Segment:
    if url.startswith(("http://", "https://")):
        return Link(url=url, label=label if label and label != url else None)
    if url.startswith("mailto:"):
        return label or url.removeprefix("mailto:")
    return label or url


def _slack_unescape(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _slack_bot_ids(payload: Mapping[str, object]) -> frozenset[str]:
    """The bot's own user id, as the Events API envelope authorizes it."""
    found: set[str] = set()
    authorizations = payload.get("authorizations")
    if isinstance(authorizations, list):
        for authorization in authorizations:
            if isinstance(authorization, dict) and authorization.get("is_bot", True):
                user_id = authorization.get("user_id")
                if isinstance(user_id, str) and user_id.strip():
                    found.add(user_id.strip())
    return frozenset(found)


# -- Teams ---------------------------------------------------------------------

_TEAMS_AT = re.compile(r"<at\b[^>]*>(.*?)</at>", re.IGNORECASE | re.DOTALL)
_HTML_LINK = re.compile(
    r"""<a\b[^>]*\bhref\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""",
    re.IGNORECASE | re.DOTALL,
)
_HTML_TAG = re.compile(r"<[^>]+>")


def teams_markup(text: str, *, activity: Mapping[str, object]) -> Markup:
    """A Teams activity's ``<at>`` mentions, matched to its ``entities``, and
    its HTML links.

    ``text`` is what the parser made of the activity (its HTML with the tags
    taken out, then any attachment notes); the segments are read from the
    activity's own HTML, and whatever ``text`` adds after it is kept.
    """
    raw = activity.get("text")
    if not isinstance(raw, str) or "<" not in raw:
        return plain(text)
    stripped = _HTML_TAG.sub("", raw).strip()
    if not text.startswith(stripped):
        return plain(text)
    rest = text[len(stripped) :]
    mentions = list(_teams_mentions(activity))
    bot_id = _recipient_id(activity)
    segments: list[Segment] = []
    position = 0
    for match in _TEAMS_AT.finditer(raw):
        segments.extend(_html_segments(raw[position : match.start()]))
        shown = html.unescape(_HTML_TAG.sub("", match.group(1))).strip()
        mentioned = _take_mention(mentions, match.group(0), shown)
        if mentioned is None:
            segments.append(shown)
        else:
            platform_id, person_id, name = mentioned
            segments.append(
                Named(
                    external_id=person_id,
                    label=name or shown or None,
                    is_bot=bool(bot_id) and platform_id == bot_id,
                )
            )
        position = match.end()
    segments.extend(_html_segments(raw[position:]))
    _strip_ends(segments)
    segments.append(rest)
    return _joined(segments)


def _html_segments(fragment: str) -> list[Segment]:
    segments: list[Segment] = []
    position = 0
    for match in _HTML_LINK.finditer(fragment):
        segments.append(_html_text(fragment[position : match.start()]))
        url = html.unescape(match.group(1)).strip()
        label = _html_text(match.group(2)).strip()
        if url.startswith(("http://", "https://")):
            segments.append(Link(url=url, label=label if label != url else None))
        else:
            segments.append(label)
        position = match.end()
    segments.append(_html_text(fragment[position:]))
    return segments


def _html_text(fragment: str) -> str:
    return html.unescape(_HTML_TAG.sub("", fragment)).replace("\xa0", " ")


@dataclass(frozen=True, slots=True)
class _TeamsMention:
    #: The entity's ``<at>…</at>`` text.
    text: str
    #: The Bot Framework id (``29:…``; the bot is ``28:…``).
    platform_id: str
    #: Who the person is: their AAD object id where the entity carries it, as
    #: Teams people are resolved by; else the Bot Framework id.
    person_id: str
    name: str | None


def _teams_mentions(activity: Mapping[str, object]) -> list[_TeamsMention]:
    """Each mention entity of the activity."""
    entities = activity.get("entities")
    if not isinstance(entities, list):
        return []
    found: list[_TeamsMention] = []
    for entity in entities:
        if not isinstance(entity, dict) or entity.get("type") != "mention":
            continue
        mentioned = entity.get("mentioned")
        if not isinstance(mentioned, dict):
            continue
        external_id = mentioned.get("id")
        if not isinstance(external_id, str) or not external_id.strip():
            continue
        name = mentioned.get("name")
        shown = entity.get("text")
        aad = mentioned.get("aadObjectId")
        found.append(
            _TeamsMention(
                text=shown if isinstance(shown, str) else "",
                platform_id=external_id.strip(),
                person_id=(
                    aad.strip()
                    if isinstance(aad, str) and aad.strip()
                    else external_id.strip()
                ),
                name=name.strip() if isinstance(name, str) and name.strip() else None,
            )
        )
    return found


def _take_mention(
    mentions: list[_TeamsMention], tag: str, shown: str
) -> tuple[str, str, str | None] | None:
    """The entity this ``<at>`` tag is -- ``(platform id, person id, name)``:
    the one with its exact text, else the first with its name. Taken, so a
    name said twice matches two entities."""
    for matches in (
        lambda mention: mention.text == tag,
        lambda mention: mention.name is not None and mention.name == shown,
    ):
        for index, mention in enumerate(mentions):
            if matches(mention):
                del mentions[index]
                return mention.platform_id, mention.person_id, mention.name
    return None


def _recipient_id(activity: Mapping[str, object]) -> str | None:
    recipient = activity.get("recipient")
    if isinstance(recipient, dict):
        found = recipient.get("id")
        if isinstance(found, str) and found.strip():
            return found.strip()
    return None


# -- Telegram ------------------------------------------------------------------


def telegram_markup(text: str, *, update: Mapping[str, object]) -> Markup:
    """A Telegram message's ``text_mention``, ``text_link`` and ``url``
    entities. Their offsets count UTF-16 code units, and are only read for a
    message whose text is the whole of ``text`` (not a batch of several)."""
    message = _telegram_message(update)
    if message is None:
        return plain(text)
    own = message.get("text") or message.get("caption")
    entities = message.get("entities") or message.get("caption_entities")
    if own != text or not isinstance(entities, list):
        return plain(text)
    units = _Utf16(text)
    marked = sorted(_telegram_marks(entities, units), key=lambda mark: mark[0])
    if not marked:
        return plain(text)
    segments: list[Segment] = []
    position = 0
    for start, end, segment in marked:
        if start < position:
            continue  # overlapping entities: the first one wins
        segments.append(units.piece(position, start))
        segments.append(segment)
        position = end
    segments.append(units.piece(position, units.length))
    return _joined(segments)


class _Utf16:
    """Text sliced the way Telegram counts it: in UTF-16 code units."""

    def __init__(self, text: str) -> None:
        self.encoded = text.encode("utf-16-le")
        self.length = len(self.encoded) // 2

    def piece(self, start: int, end: int) -> str:
        return self.encoded[start * 2 : end * 2].decode("utf-16-le", errors="replace")


def _telegram_marks(
    entities: list[object], units: _Utf16
) -> list[tuple[int, int, Segment]]:
    """``(start, end, segment)`` for each entity this reads, in range."""
    marked: list[tuple[int, int, Segment]] = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        offset, length = entity.get("offset"), entity.get("length")
        if not isinstance(offset, int) or not isinstance(length, int):
            continue
        if offset < 0 or length <= 0 or offset + length > units.length:
            continue
        segment = _telegram_segment(entity, units.piece(offset, offset + length))
        if segment is not None:
            marked.append((offset, offset + length, segment))
    return marked


def _telegram_segment(entity: dict[str, object], shown: str) -> Segment | None:
    kind = entity.get("type")
    if kind == "text_mention":
        user = entity.get("user")
        user_id = user.get("id") if isinstance(user, dict) else None
        if user_id is None:
            return None
        is_bot = bool(user.get("is_bot")) if isinstance(user, dict) else False
        return Named(
            external_id=str(user_id), label=shown.lstrip("@") or None, is_bot=is_bot
        )
    if kind == "text_link":
        url = entity.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return Link(url=url, label=shown if shown != url else None)
        return None
    if kind == "url":
        url = shown if shown.startswith(("http://", "https://")) else f"https://{shown}"
        return Link(url=url, label=shown if shown != url else None)
    return None


def _telegram_message(update: Mapping[str, object]) -> Mapping[str, object] | None:
    for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
        found = update.get(key)
        if isinstance(found, dict):
            return found
    return None


# -- WhatsApp ------------------------------------------------------------------

#: WhatsApp writes a mention into the body as ``@`` and the person's number.
_WHATSAPP_MENTION = re.compile(r"(?<![\w.+-])@(\d{6,15})\b")


def whatsapp_markup(text: str) -> Markup:
    segments: list[Segment] = []
    position = 0
    for match in _WHATSAPP_MENTION.finditer(text):
        segments.append(text[position : match.start()])
        segments.append(Named(external_id=match.group(1)))
        position = match.end()
    segments.append(text[position:])
    return _joined(segments)


# -- shared --------------------------------------------------------------------


def _strip_ends(segments: list[Segment]) -> None:
    """Trim the text at both ends, as the parser trims the whole."""
    if segments and isinstance(segments[0], str):
        segments[0] = segments[0].lstrip()
    if segments and isinstance(segments[-1], str):
        segments[-1] = segments[-1].rstrip()


def _joined(segments: Sequence[Segment]) -> Markup:
    """Adjacent text run together, empty text dropped."""
    out: list[Segment] = []
    for segment in segments:
        if isinstance(segment, str):
            if not segment:
                continue
            if out and isinstance(out[-1], str):
                out[-1] = out[-1] + segment
                continue
        out.append(segment)
    return Markup(segments=tuple(out) or ("",))
