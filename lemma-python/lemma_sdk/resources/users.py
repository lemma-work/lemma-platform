from __future__ import annotations

from typing import Any

from ..openapi_client.api.users import user_profile_get, user_profile_upsert
from ..openapi_client.api.users import user_email_delivery_get, user_email_delivery_test
from ..openapi_client.api.users import users_ensure_first_workspace, users_work_set
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
from ..openapi_client.models.work_answer_request import WorkAnswerRequest
from ..openapi_client.types import UNSET
from .base import Resource


class User(Resource):
    def ensure_first_workspace(
        self,
        *,
        with_pod: bool = True,
        own_organization: bool = False,
        team: bool | None = None,
    ) -> FirstWorkspaceResponse:
        """Put yourself in an organization, making one if you belong to none.

        ``team=None`` lets a verified work domain decide what kind is made.
        """
        return self._call(
            users_ensure_first_workspace,
            body=FirstWorkspaceRequest(
                with_pod=with_pod,
                own_organization=own_organization,
                team=UNSET if team is None else team,
            ),
        )

    def set_work(
        self,
        request: WorkAnswerRequest | dict[str, Any],
    ) -> UserResponse:
        """Keep what you do; ``{"jobs": []}`` records that you skipped it."""
        return self._call(
            users_work_set,
            body=request,
            body_model=WorkAnswerRequest,
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
