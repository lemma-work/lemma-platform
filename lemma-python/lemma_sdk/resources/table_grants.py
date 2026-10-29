from __future__ import annotations

from ..openapi_client.api.channels import (
    channel_table_grant_list,
    channel_table_grant_remove,
    channel_table_grant_set,
)
from ..openapi_client.models.table_grant_request import TableGrantRequest
from ..openapi_client.models.table_grant_response import TableGrantResponse
from ..openapi_client.models.table_grants_response import TableGrantsResponse
from .base import BoundResource, as_uuid


class ChannelTableGrants(BoundResource):
    """Tables a group's people outside the pod may read, or add rows to:
    ``pod.channels.table_grants``.

    A group's owners or an admin give it one table at a time -- the columns
    everybody there may read, and the columns they may fill in a new row --
    through their own access to the table. Everybody in the group reads what is
    granted, the people outside the pod included.
    """

    def list(self, channel_id: str) -> TableGrantsResponse:
        """The tables this group may read or add to."""
        return self._call(
            channel_table_grant_list, self._pod_uuid(), as_uuid(channel_id)
        )

    def set(
        self,
        channel_id: str,
        table: str,
        *,
        read_columns: list[str] | None = None,
        add_columns: list[str] | None = None,
    ) -> TableGrantResponse:
        """Give the group these columns of ``table`` to read, and those to fill."""
        return self._call(
            channel_table_grant_set,
            self._pod_uuid(),
            as_uuid(channel_id),
            table,
            body={
                "read_columns": list(read_columns or []),
                "add_columns": list(add_columns or []),
            },
            body_model=TableGrantRequest,
        )

    def remove(self, channel_id: str, table: str) -> None:
        """The group no longer reads or adds to ``table``."""
        self._call(
            channel_table_grant_remove, self._pod_uuid(), as_uuid(channel_id), table
        )
