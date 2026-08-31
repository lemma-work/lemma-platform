from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.agent_surface_status import AgentSurfaceStatus
from ..models.surface_platform import SurfacePlatform
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.surface_admin_consent_info import SurfaceAdminConsentInfo
    from ..models.surface_platform_setup_guide import SurfacePlatformSetupGuide
    from ..models.surface_setup_action import SurfaceSetupAction


T = TypeVar("T", bound="SurfaceSetupResponse")


@_attrs_define
class SurfaceSetupResponse:
    """Everything a caller needs to finish setting up a surface, in one read.

    Merges the former setup-status, admin-consent, and platform-checklist
    endpoints. Works both before a surface exists (`exists=False`, guide only)
    and after.

    ``ready`` is True when the user has nothing left to do (system credentials,
    or an already-granted consent). ``actions`` is populated *only* when the
    user must act — e.g. point their own Slack/Teams/WhatsApp app at Lemma —
    so the UI can show a clean "Ready" state otherwise.

        Attributes:
            exists (bool):
            guide (SurfacePlatformSetupGuide):
            platform (SurfacePlatform): The platforms a pod can be reached on.

                Email is Resend, and only Resend. Gmail and Outlook were here as
                Composio-backed mailboxes, which made "an email surface" mean three
                different transports with three attachment strategies between them -- bytes,
                Graph drafts, and a signed URL the provider downloads server-side. Reaching
                a Gmail *account* is still something an agent does, through the connector;
                it is just not a surface.
            status (AgentSurfaceStatus):
            actions (list[SurfaceSetupAction] | Unset):
            admin_consent (None | SurfaceAdminConsentInfo | Unset):
            ready (bool | Unset):  Default: False.
            webhook_url (None | str | Unset):
    """

    exists: bool
    guide: SurfacePlatformSetupGuide
    platform: SurfacePlatform
    status: AgentSurfaceStatus
    actions: list[SurfaceSetupAction] | Unset = UNSET
    admin_consent: None | SurfaceAdminConsentInfo | Unset = UNSET
    ready: bool | Unset = False
    webhook_url: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.surface_admin_consent_info import SurfaceAdminConsentInfo

        exists = self.exists

        guide = self.guide.to_dict()

        platform = self.platform.value

        status = self.status.value

        actions: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.actions, Unset):
            actions = []
            for actions_item_data in self.actions:
                actions_item = actions_item_data.to_dict()
                actions.append(actions_item)

        admin_consent: dict[str, Any] | None | Unset
        if isinstance(self.admin_consent, Unset):
            admin_consent = UNSET
        elif isinstance(self.admin_consent, SurfaceAdminConsentInfo):
            admin_consent = self.admin_consent.to_dict()
        else:
            admin_consent = self.admin_consent

        ready = self.ready

        webhook_url: None | str | Unset
        if isinstance(self.webhook_url, Unset):
            webhook_url = UNSET
        else:
            webhook_url = self.webhook_url

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "exists": exists,
                "guide": guide,
                "platform": platform,
                "status": status,
            }
        )
        if actions is not UNSET:
            field_dict["actions"] = actions
        if admin_consent is not UNSET:
            field_dict["admin_consent"] = admin_consent
        if ready is not UNSET:
            field_dict["ready"] = ready
        if webhook_url is not UNSET:
            field_dict["webhook_url"] = webhook_url

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.surface_admin_consent_info import SurfaceAdminConsentInfo
        from ..models.surface_platform_setup_guide import SurfacePlatformSetupGuide
        from ..models.surface_setup_action import SurfaceSetupAction

        d = dict(src_dict)
        exists = d.pop("exists")

        guide = SurfacePlatformSetupGuide.from_dict(d.pop("guide"))

        platform = SurfacePlatform(d.pop("platform"))

        status = AgentSurfaceStatus(d.pop("status"))

        _actions = d.pop("actions", UNSET)
        actions: list[SurfaceSetupAction] | Unset = UNSET
        if _actions is not UNSET:
            actions = []
            for actions_item_data in _actions:
                actions_item = SurfaceSetupAction.from_dict(actions_item_data)

                actions.append(actions_item)

        def _parse_admin_consent(
            data: object,
        ) -> None | SurfaceAdminConsentInfo | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                admin_consent_type_0 = SurfaceAdminConsentInfo.from_dict(data)

                return admin_consent_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | SurfaceAdminConsentInfo | Unset, data)

        admin_consent = _parse_admin_consent(d.pop("admin_consent", UNSET))

        ready = d.pop("ready", UNSET)

        def _parse_webhook_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        webhook_url = _parse_webhook_url(d.pop("webhook_url", UNSET))

        surface_setup_response = cls(
            exists=exists,
            guide=guide,
            platform=platform,
            status=status,
            actions=actions,
            admin_consent=admin_consent,
            ready=ready,
            webhook_url=webhook_url,
        )

        surface_setup_response.additional_properties = d
        return surface_setup_response

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
