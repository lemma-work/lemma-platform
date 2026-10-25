from __future__ import annotations

from datetime import datetime

from ..openapi_client.api.organizations import (
    org_apps_list,
    org_create,
    org_get,
    org_home,
    org_home_seen_get,
    org_home_seen_set,
    org_list,
    org_member_list,
    org_navigation,
    org_needs_you_list,
    org_permissions_me,
    org_recents_get,
)
from ..openapi_client.models.home_seen_request import HomeSeenRequest
from ..openapi_client.models.home_seen_response import HomeSeenResponse
from ..openapi_client.models.navigation_response import NavigationResponse
from ..openapi_client.models.needs_you_response import NeedsYouResponse
from ..openapi_client.models.organization_app_list_response import (
    OrganizationAppListResponse,
)
from ..openapi_client.models.organization_create_request import (
    OrganizationCreateRequest,
)
from ..openapi_client.models.organization_effective_permissions_response import (
    OrganizationEffectivePermissionsResponse,
)
from ..openapi_client.models.organization_home_response import OrganizationHomeResponse
from ..openapi_client.models.organization_list_response import OrganizationListResponse
from ..openapi_client.models.organization_member_list_response import (
    OrganizationMemberListResponse,
)
from ..openapi_client.models.organization_recents_response import (
    OrganizationRecentsResponse,
)
from ..openapi_client.models.organization_response import OrganizationResponse
from ..openapi_client.types import UNSET
from .base import BoundResource, Resource, as_uuid


class Orgs(Resource):
    def list(
        self, *, limit: int = 100, page_token: str | None = None
    ) -> OrganizationListResponse:
        """One page of the organizations you belong to.

        ``next_page_token`` is the only way past ``limit``; a caller resolving a
        name against this list sees a prefix without it, and reports "not found"
        for something that is there.
        """
        return self._call(
            org_list,
            limit=limit,
            page_token=page_token if page_token is not None else UNSET,
        )

    def create(self, *, name: str) -> OrganizationResponse:
        return self._call(
            org_create,
            body={"name": name},
            body_model=OrganizationCreateRequest,
        )

    def get(self, org_id: str) -> OrganizationResponse:
        return self._call(org_get, as_uuid(org_id))

    def navigation(self) -> NavigationResponse:
        """Every organization you belong to, each with the pods you can see.

        One call instead of listing organizations and then a pod list per
        organization. Names and ids only; use :meth:`home` for one
        organization's apps, agents and roles.
        """
        return self._call(org_navigation)

    def home(self, org_id: str) -> OrganizationHomeResponse:
        """One organization's pods, with their apps, agents and your roles."""
        return self._call(org_home, as_uuid(org_id))

    def needs_you(self, org_id: str, *, limit: int = 100) -> NeedsYouResponse:
        """Everything waiting on you in every pod you may open, oldest first.

        Questions, approvals and permission requests in your conversations,
        workflow forms and decisions assigned to you, and contacts waiting on a
        person where you look after contacts -- one call, not one per pod.
        """
        return self._call(org_needs_you_list, as_uuid(org_id), limit=limit)

    def recents(
        self,
        org_id: str,
        *,
        days: int = 14,
        chats: int = 10,
        timezone: str = "UTC",
    ) -> OrganizationRecentsResponse:
        """Each pod's latest chats, the automations that ran, and your
        conversations per day in ``timezone`` (an IANA name)."""
        return self._call(
            org_recents_get,
            as_uuid(org_id),
            days=days,
            chats=chats,
            timezone=timezone,
        )

    def apps(self, org_id: str) -> OrganizationAppListResponse:
        """Every app you may open across the organization's pods, newest first."""
        return self._call(org_apps_list, as_uuid(org_id))

    def home_seen(self, org_id: str) -> HomeSeenResponse:
        """When you last marked the organization's Home seen."""
        return self._call(org_home_seen_get, as_uuid(org_id))

    def mark_home_seen(
        self, org_id: str, *, seen_at: datetime | None = None
    ) -> HomeSeenResponse:
        """Mark Home seen as of ``seen_at`` (aware), or now. It never moves back."""
        if seen_at is None:
            return self._call(org_home_seen_set, as_uuid(org_id))
        return self._call(
            org_home_seen_set,
            as_uuid(org_id),
            body={"seen_at": seen_at.isoformat()},
            body_model=HomeSeenRequest,
        )

    def permissions(self, org_id: str) -> OrganizationEffectivePermissionsResponse:
        """The organization-level permission ids you hold in ``org_id``."""
        return self._call(org_permissions_me, as_uuid(org_id))


class BoundOrg(BoundResource):
    def get(self) -> OrganizationResponse:
        return self._call(org_get, self._org_uuid())

    def members(self, *, limit: int = 100) -> OrganizationMemberListResponse:
        return self._call(org_member_list, self._org_uuid(), limit=limit)

    def home(self) -> OrganizationHomeResponse:
        """This organization's pods, with their apps, agents and your roles."""
        return self._call(org_home, self._org_uuid())

    def permissions(self) -> OrganizationEffectivePermissionsResponse:
        """The organization-level permission ids you hold here, such as
        ``org.installation.manage``."""
        return self._call(org_permissions_me, self._org_uuid())
