from __future__ import annotations

from uuid import UUID

from ..openapi_client.api.contact_outreach import contact_outreach_send
from ..openapi_client.api.contacts import (
    contact_delete,
    contact_export,
    contact_get,
    contact_list,
    contact_update,
)
from ..openapi_client.models.contact_export_response import ContactExportResponse
from ..openapi_client.models.contact_list_response import ContactListResponse
from ..openapi_client.models.contact_response import ContactResponse
from ..openapi_client.models.contact_update_request import ContactUpdateRequest
from ..openapi_client.models.outreach_request import OutreachRequest
from ..openapi_client.models.outreach_response import OutreachResponse
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
        self, *, limit: int = 50, cursor: str | None = None
    ) -> ContactListResponse:
        """The pod's contacts, newest first.

        Page by passing the last page's ``next_cursor`` as ``cursor``; it is
        opaque, and absent on the last page.
        """
        return self._call(
            contact_list,
            self._pod_uuid(),
            limit=limit,
            cursor=cursor if cursor is not None else UNSET,
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

    def outreach(
        self,
        contact_id: str | UUID,
        *,
        handle: str,
        text: str,
        kind: str | None = None,
        client_message_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> OutreachResponse:
        """Write to a contact first, on one of their handles.

        Email any time; WhatsApp only within 24 hours of their last message
        there; never a handle that asked the pod to stop. The line goes in the
        contact's channel at that handle's place -- opened if new -- and is
        sent from there: the response is the line and its delivery, ``PENDING``
        until it goes. ``kind`` (``EMAIL``, ``PHONE``, ...) only when the value
        could name two handles; the same ``client_message_id`` (or
        ``idempotency_key`` from the same caller) twice is one line.
        """
        body: dict[str, object] = {"handle": handle, "text": text}
        if kind is not None:
            body["kind"] = kind
        if client_message_id is not None:
            body["client_message_id"] = client_message_id
        return self._call(
            contact_outreach_send,
            self._pod_uuid(),
            as_uuid(contact_id),
            body=body,
            body_model=OutreachRequest,
            idempotency_key=(idempotency_key if idempotency_key is not None else UNSET),
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
