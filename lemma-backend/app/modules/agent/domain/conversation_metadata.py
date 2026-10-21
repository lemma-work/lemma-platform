"""The keys of a conversation's (or a run's) metadata JSON that code decides on.

Metadata is free-form JSON, and most of it is a client's business. A few keys
are not: where the conversation works (``cwd``), what opened it (``source``),
and which workflow run it belongs to (``workflow_run_id``). Read as
``metadata.get("cwd")`` at each site, each read re-decided what a missing or
mistyped value means. These models read them once, typed, and leniently: the
JSON was written by every release since the key existed, and one unreadable
value is read as absent rather than failing whatever asked.

Views only -- the stored JSON is unchanged, and writers still write the plain
keys, through ``origin_metadata`` where the origin is concerned.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict

CWD_KEY = "cwd"
SOURCE_KEY = "source"
WORKFLOW_RUN_ID_KEY = "workflow_run_id"
#: The ``source`` of a run started by ``POST .../retry``. Such a run owns no
#: message of its own: it replays the person's turn, which stays on the run it
#: retried.
MANUAL_RETRY_SOURCE = "manual_retry"


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _uuid(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except ValueError:
        return None


LenientText = Annotated[str | None, BeforeValidator(_text)]
LenientUuid = Annotated[UUID | None, BeforeValidator(_uuid)]


class ConversationMetadataFields(BaseModel):
    """The typed keys of a conversation's metadata. Everything else is ignored."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    #: The workspace directory this conversation works in, once recorded.
    cwd: LenientText = None
    #: What opened it, when something other than a person in the app did.
    source: LenientText = None
    #: The workflow run it is a step of.
    workflow_run_id: LenientUuid = None

    @classmethod
    def of(cls, metadata: object) -> ConversationMetadataFields:
        if not isinstance(metadata, Mapping):
            return cls()
        return cls.model_validate(
            {
                key: metadata.get(key)
                for key in (CWD_KEY, SOURCE_KEY, WORKFLOW_RUN_ID_KEY)
                if metadata.get(key) is not None
            }
        )


def source_of(metadata: object) -> str | None:
    """``source`` off any metadata JSON -- a conversation's, a run's, a message's."""
    if not isinstance(metadata, Mapping):
        return None
    return _text(metadata.get(SOURCE_KEY))


def origin_metadata(*, source: str, workflow_run_id: UUID | None) -> dict[str, object]:
    """The keys that say what opened a conversation, as they are stored."""
    stamped: dict[str, object] = {SOURCE_KEY: source}
    if workflow_run_id is not None:
        stamped[WORKFLOW_RUN_ID_KEY] = str(workflow_run_id)
    return stamped
