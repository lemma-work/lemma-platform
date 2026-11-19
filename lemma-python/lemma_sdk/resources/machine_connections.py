from __future__ import annotations

from uuid import UUID

from ..openapi_client.api.machines import (
    machine_connections_delete,
    machine_connections_list,
    machine_connections_pair,
    machine_connections_pairing,
)
from ..openapi_client.models.approve_pairing_request import ApprovePairingRequest
from ..openapi_client.models.machine_connection_list import MachineConnectionList
from ..openapi_client.models.machine_connection_response import (
    MachineConnectionResponse,
)
from ..openapi_client.models.pending_pairing_response import PendingPairingResponse
from .base import Resource, as_uuid


class MachineConnections(Resource):
    """Machines you joined to Lemma with ``lemma machine connect``.

    A connected machine belongs to the person who approved it, so nothing here
    takes a pod or an organization; sharing one into a pod is decided when it is
    approved.
    """

    def list(self) -> MachineConnectionList:
        """Your connected machines, with whether each is online right now."""
        return self._call(machine_connections_list)

    def pairing(self, code: str) -> PendingPairingResponse:
        """What a machine showing ``code`` calls itself, before approving it."""
        return self._call(machine_connections_pairing, code)

    def pair(
        self,
        code: str,
        *,
        name: str | None = None,
        pod_id: str | UUID | None = None,
    ) -> MachineConnectionResponse:
        """Approve the machine showing ``code``; it claims its secret itself."""
        return self._call(
            machine_connections_pair,
            body=ApprovePairingRequest(
                code=code,
                name=name,
                pod_id=as_uuid(pod_id) if pod_id is not None else None,
            ),
        )

    def delete(self, connection_id: str | UUID) -> None:
        """Disconnect a machine: its secret stops working and its link closes."""
        self._call(machine_connections_delete, as_uuid(connection_id))
