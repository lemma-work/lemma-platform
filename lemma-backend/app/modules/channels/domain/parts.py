"""What a line carries beside its text: who it names, files, links.

Typed, so a client cannot store arbitrary JSON every reader is then served,
and so a platform's markup (``<@U123>``, ``<url|label>``, Teams ``<at>``) is
read once, at ingest, into the same shapes a member's line in Lemma has.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

#: The most parts one line may carry.
MAX_PARTS = 20


class MentionPart(BaseModel):
    """Somebody the line names, resolved: a member, a contact or an agent."""

    model_config = ConfigDict(frozen=True)

    type: Literal["mention"]
    kind: Literal["MEMBER", "CONTACT", "AGENT"]
    id: UUID
    #: The name as it appeared, for a reader who cannot resolve the id.
    name: str | None = Field(default=None, max_length=255)


class FilePart(BaseModel):
    """A file of the pod's the line shares.

    Whoever writes the line must be able to read the file; it is then read
    through the line (``GET …/messages/{m}/files/{file_id}``), by whoever may
    see the line, as its writer reads it -- never through the file's own
    folder, which a private channel's readers need not share.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal["file"]
    #: The pod file's path as written (``/me/…`` is the writer's own); stored
    #: as the file's own path.
    path: str = Field(min_length=1, max_length=1024)
    #: The file, as the line was written: set by the server, not the client.
    file_id: UUID | None = None
    name: str = Field(min_length=1, max_length=255)
    mime_type: str | None = Field(default=None, max_length=255)
    size: int | None = Field(default=None, ge=0)


class LinkPart(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["link"]
    url: str = Field(min_length=1, max_length=2048, pattern=r"^https?://")
    label: str | None = Field(default=None, max_length=255)


Part = Annotated[MentionPart | FilePart | LinkPart, Field(discriminator="type")]

_PARTS = TypeAdapter(list[Part])


def read_parts(raw: object) -> tuple[Part, ...]:
    """Parts as stored; anything that is not a part is dropped, not served."""
    if not isinstance(raw, list):
        return ()
    kept: list[Part] = []
    for item in raw[:MAX_PARTS]:
        try:
            kept.extend(_PARTS.validate_python([item]))
        except ValueError:
            continue
    return tuple(kept)


def dump_parts(parts: tuple[Part, ...] | list[Part]) -> list[dict[str, object]]:
    return [part.model_dump(mode="json", exclude_none=True) for part in parts]
