from __future__ import annotations

from typing import Any

from ..openapi_client.api.calls import call_config, call_open
from ..openapi_client.models.call_config_response import CallConfigResponse
from ..openapi_client.models.call_response import CallResponse
from ..openapi_client.models.open_call_request import OpenCallRequest
from .base import BoundResource


class PodCalls(BoundResource):
    """Live voice calls with the pod's agents.

    A call is a conversation: opening one returns its id, and the audio runs
    over the WebSocket at ``/pods/{pod_id}/calls/{call_id}/socket``::

        if pod.calls.config().engines:
            call = pod.calls.open(display_name="Ria")
    """

    def config(self) -> CallConfigResponse:
        """The voice engines this server can open a call with, the default first."""
        return self._call(call_config, self._pod_uuid())

    def open(
        self,
        *,
        agent_name: str | None = None,
        display_name: str = "your teammate",
        title: str | None = None,
    ) -> CallResponse:
        """Open a call with ``agent_name``, or the pod's default agent."""
        body: dict[str, Any] = {"display_name": display_name}
        if agent_name is not None:
            body["agent_name"] = agent_name
        if title is not None:
            body["title"] = title
        return self._call(
            call_open, self._pod_uuid(), body=body, body_model=OpenCallRequest
        )
