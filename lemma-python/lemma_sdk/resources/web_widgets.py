from __future__ import annotations

from uuid import UUID

from ..openapi_client.api.agent_surfaces import (
    agent_web_widget_create,
    agent_web_widget_delete,
    agent_web_widget_list,
    agent_web_widget_rotate_secret,
    agent_web_widget_update,
)
from ..openapi_client.models.web_widget_create_request import WebWidgetCreateRequest
from ..openapi_client.models.web_widget_created_response import (
    WebWidgetCreatedResponse,
)
from ..openapi_client.models.web_widget_list_response import WebWidgetListResponse
from ..openapi_client.models.web_widget_response import WebWidgetResponse
from ..openapi_client.models.web_widget_secret_response import WebWidgetSecretResponse
from ..openapi_client.models.web_widget_update_request import WebWidgetUpdateRequest
from .base import BoundResource, as_uuid


class PodWebWidgets(BoundResource):
    """A pod's chat bubbles and forms for other people's web pages.

    A widget's public key goes in the page and names the widget, nothing more:
    anonymous visitors chat as outsiders. Its signing secret stays on the
    customer's server, which signs short-lived tokens naming its own signed-in
    users, who are then answered as contacts. ``create`` and ``rotate_secret``
    return the secret once.
    """

    def list(self) -> WebWidgetListResponse:
        return self._call(agent_web_widget_list, self._pod_uuid())

    def create(
        self, request: WebWidgetCreateRequest | dict
    ) -> WebWidgetCreatedResponse:
        return self._call(
            agent_web_widget_create,
            self._pod_uuid(),
            body=request,
            body_model=WebWidgetCreateRequest,
        )

    def update(
        self, widget_id: str | UUID, request: WebWidgetUpdateRequest | dict
    ) -> WebWidgetResponse:
        return self._call(
            agent_web_widget_update,
            self._pod_uuid(),
            as_uuid(widget_id),
            body=request,
            body_model=WebWidgetUpdateRequest,
        )

    def rotate_secret(self, widget_id: str | UUID) -> WebWidgetSecretResponse:
        """A new signing secret. Tokens signed with the old one stop working."""
        return self._call(
            agent_web_widget_rotate_secret, self._pod_uuid(), as_uuid(widget_id)
        )

    def delete(self, widget_id: str | UUID) -> None:
        self._call(agent_web_widget_delete, self._pod_uuid(), as_uuid(widget_id))
