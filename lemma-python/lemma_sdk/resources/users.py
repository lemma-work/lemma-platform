from __future__ import annotations

from typing import Any

from ..openapi_client.api.users import user_profile_get, user_profile_upsert
from ..openapi_client.api.users import user_email_delivery_get, user_email_delivery_test
from ..openapi_client.api.users import users_ensure_first_workspace
from ..openapi_client.models.email_delivery_status_response import (
    EmailDeliveryStatusResponse,
)
from ..openapi_client.models.email_delivery_test_response import (
    EmailDeliveryTestResponse,
)
from ..openapi_client.models.first_workspace_request import FirstWorkspaceRequest
from ..openapi_client.models.first_workspace_response import FirstWorkspaceResponse
from ..openapi_client.models.user_profile_request import UserProfileRequest
from ..openapi_client.models.user_response import UserResponse
from .base import Resource


class User(Resource):
    def ensure_first_workspace(
        self, *, with_pod: bool = True
    ) -> FirstWorkspaceResponse:
        return self._call(
            users_ensure_first_workspace, body=FirstWorkspaceRequest(with_pod=with_pod)
        )

    def email_delivery(self) -> EmailDeliveryStatusResponse:
        """Whether this local installation can send email. 404 on a server."""
        return self._call(user_email_delivery_get)

    def send_test_email(self) -> EmailDeliveryTestResponse:
        """Send a test email to your own address. Local installations only."""
        return self._call(user_email_delivery_test)

    def profile(self) -> UserResponse:
        return self._call(user_profile_get)

    def update_profile(
        self,
        request: UserProfileRequest | dict[str, Any],
    ) -> UserResponse:
        return self._call(
            user_profile_upsert,
            body=request,
            body_model=UserProfileRequest,
        )
