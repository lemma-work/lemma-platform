from __future__ import annotations

from datetime import datetime
from uuid import UUID

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
        self, *, limit: int = 50, before: datetime | None = None
    ) -> ContactListResponse:
        """The pod's contacts, newest first. Page with ``next_before``."""
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

    def export(self, contact_id: str | UUID) -> ContactExportResponse:
        """Everything the pod holds about a contact. Takes a pod admin."""
        return self._call(contact_export, self._pod_uuid(), as_uuid(contact_id))

    def delete(self, contact_id: str | UUID) -> None:
        """Forget a contact, their handles and their conversations. Takes a pod admin."""
        self._call(contact_delete, self._pod_uuid(), as_uuid(contact_id))
