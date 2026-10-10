from __future__ import annotations

from typing import Literal
from uuid import UUID

from ..openapi_client.api.contacts import (
    contact_delete,
    contact_export,
    contact_follow_up,
    contact_get,
    contact_list,
    contact_update,
)
from ..openapi_client.models.contact_export_response import ContactExportResponse
from ..openapi_client.models.contact_list_response import ContactListResponse
from ..openapi_client.models.follow_up_request import FollowUpRequest
from ..openapi_client.models.follow_up_response import FollowUpResponse
from ..openapi_client.models.contact_response import ContactResponse
from ..openapi_client.models.contact_update_request import ContactUpdateRequest
from ..openapi_client.types import UNSET
from .base import BoundResource, as_uuid


class PodContacts(BoundResource):
    """The people a pod's bots answer who are not members.

    A contact is somebody who wrote to one of the pod's own bots privately --
    from WhatsApp, Telegram or an authenticated email address -- while the bot
    was set to answer contacts. They never sign in and hold no grant; they are
    answered from what the pod made Public. ``delete`` forgets a contact and
    the handles they are known by.
    """

    def list(
        self, *, limit: int = 50, before: str | None = None
    ) -> ContactListResponse:
        """The pod's contacts, newest first.

        Page by passing the last page's ``next_before`` as ``before``; it is
        opaque, and absent on the last page.
        """
        return self._call(
            contact_list,
            self._pod_uuid(),
            limit=limit,
            before=before if before is not None else UNSET,
        )

    def get(self, contact_id: str | UUID) -> ContactResponse:
        """One contact, with the handles they are known by."""
        return self._call(contact_get, self._pod_uuid(), as_uuid(contact_id))

    def rename(
        self, contact_id: str | UUID, display_name: str | None
    ) -> ContactResponse:
        """Change the name a contact is addressed by."""
        return self._call(
            contact_update,
            self._pod_uuid(),
            as_uuid(contact_id),
            body={"display_name": display_name},
            body_model=ContactUpdateRequest,
        )

    def follow_up(
        self,
        contact_id: str | UUID,
        message: str,
        *,
        channel: Literal["latest", "email"] = "latest",
    ) -> FollowUpResponse:
        """Write to a contact in their latest conversation, where the channel allows.

        ``channel="email"`` sends to their verified email address instead: from
        the pod's email address that answers contacts, in a new thread, when
        their latest conversation is a web chat or another platform's.

        Takes ``contact.message`` (editors and up). Refused when they
        unsubscribed there, when WhatsApp's 24-hour window has closed, when
        they never wrote to the pod, or past the day's follow-ups to them; for
        email, when they have no verified address or the pod has no email
        address answering contacts.
        """
        return self._call(
            contact_follow_up,
            self._pod_uuid(),
            as_uuid(contact_id),
            body={"message": message, "channel": channel},
            body_model=FollowUpRequest,
        )

    def export(
        self, contact_id: str | UUID, *, cursor: str | None = None
    ) -> ContactExportResponse:
        """One page of everything the pod holds about a contact. Takes a pod admin.

        Their conversations, then their rows in contact-owned tables. Pass the
        page's ``next_cursor`` as ``cursor`` until it is absent.
        """
        return self._call(
            contact_export,
            self._pod_uuid(),
            as_uuid(contact_id),
            cursor=cursor if cursor is not None else UNSET,
        )

    def delete(self, contact_id: str | UUID) -> None:
        """Forget a contact: their rows, handles, conversations and chat sessions.

        Takes a pod admin.
        """
        self._call(contact_delete, self._pod_uuid(), as_uuid(contact_id))
