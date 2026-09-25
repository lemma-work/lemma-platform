from __future__ import annotations

from typing import Any

from ..openapi_client.api.users import user_profile_get, user_profile_upsert
from ..openapi_client.api.users import users_ensure_first_workspace
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
