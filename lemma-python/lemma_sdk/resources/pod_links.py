from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from ..openapi_client.api.agent_asks import (
    agent_askable_pod_list,
    agent_pod_link_connect,
    agent_pod_link_disconnect,
    agent_pod_link_list,
)
from ..openapi_client.models.askable_pod_list_response import AskablePodListResponse
from ..openapi_client.models.connect_pod_request import ConnectPodRequest
from ..openapi_client.models.pod_link_list_response import PodLinkListResponse
from .base import BoundResource, as_uuid

# A link shares for reading only; the server refuses anything else.
_TABLE_READ = ["datastore.table.read", "datastore.record.read"]
_FOLDER_READ = ["folder.read"]


class PodLinks(BoundResource):
    """Which other pods this pod's assistant can ask, and which may ask it.

    A pod the caller is also in can be asked as the caller. A pod connected to
    this one can ask it with nobody present, and reads what is Public here plus
    the tables and folders shared with it, never more than reading::

        pod.pod_links.connect(other_pod_id, tables=["orders"], folders=["/specs"])
        pod.pod_links.list()           # who may ask this pod, and what they read
        pod.pod_links.disconnect(other_pod_id)

    Connecting needs ``pod.member.manage`` here and membership of the other pod;
    the caller looks after the link. Connecting again replaces what is shared.
    """

    def askable(self) -> AskablePodListResponse:
        """The other pods this pod's assistant can ask, and how."""
        return self._call(agent_askable_pod_list, self._pod_uuid())

    def list(self) -> PodLinkListResponse:
        """The other pods connected to this one, with what each was shared."""
        return self._call(agent_pod_link_list, self._pod_uuid())

    def connect(
        self,
        asking_pod_id: str | UUID,
        *,
        tables: Sequence[str] = (),
        folders: Sequence[str] = (),
    ) -> None:
        """Let ``asking_pod_id`` ask this pod, reading the tables and folders
        named here (by table name and folder path) as well as what is Public."""
        shares: list[dict[str, Any]] = [
            {
                "resource_type": "datastore_table",
                "resource_name": name,
                "permission_ids": list(_TABLE_READ),
            }
            for name in tables
        ] + [
            {
                "resource_type": "folder",
                "resource_name": path,
                "permission_ids": list(_FOLDER_READ),
            }
            for path in folders
        ]
        self._call(
            agent_pod_link_connect,
            self._pod_uuid(),
            as_uuid(asking_pod_id),
            body={"shares": shares},
            body_model=ConnectPodRequest,
        )

    def disconnect(self, asking_pod_id: str | UUID) -> None:
        """Stop ``asking_pod_id`` asking this pod and take back what it was shared."""
        self._call(agent_pod_link_disconnect, self._pod_uuid(), as_uuid(asking_pod_id))
