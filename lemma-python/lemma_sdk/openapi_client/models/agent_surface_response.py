from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.agent_surface_status import AgentSurfaceStatus
from ..models.surface_credential_mode import SurfaceCredentialMode
from ..models.surface_platform import SurfacePlatform
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.surface_config_response import SurfaceConfigResponse
    from ..models.surface_reach import SurfaceReach


T = TypeVar("T", bound="AgentSurfaceResponse")


@_attrs_define
class AgentSurfaceResponse:
    """
    Attributes:
        config (SurfaceConfigResponse): Mirrors SurfaceBehaviorConfigInput: what you send is what you get back.
        id (UUID):
        name (str):
        platform (SurfacePlatform):
        pod_id (UUID):
        account_id (None | Unset | UUID):
        agent_id (None | Unset | UUID):
        agent_name (None | str | Unset):
        credential_mode (SurfaceCredentialMode | Unset):
        reach (None | SurfaceReach | Unset):
        status (AgentSurfaceStatus | Unset):
        surface_identity_email (None | str | Unset):
        surface_identity_id (None | str | Unset):
        surface_identity_username (None | str | Unset):
        uses_default_agent (bool | Unset):  Default: False.
        webhook_url (None | str | Unset):
    """

    config: SurfaceConfigResponse
    id: UUID
    name: str
    platform: SurfacePlatform
    pod_id: UUID
    account_id: None | Unset | UUID = UNSET
    agent_id: None | Unset | UUID = UNSET
    agent_name: None | str | Unset = UNSET
    credential_mode: SurfaceCredentialMode | Unset = UNSET
    reach: None | SurfaceReach | Unset = UNSET
    status: AgentSurfaceStatus | Unset = UNSET
    surface_identity_email: None | str | Unset = UNSET
    surface_identity_id: None | str | Unset = UNSET
    surface_identity_username: None | str | Unset = UNSET
    uses_default_agent: bool | Unset = False
    webhook_url: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.surface_reach import SurfaceReach

        config = self.config.to_dict()

        id = str(self.id)

        name = self.name

        platform = self.platform.value

        pod_id = str(self.pod_id)

        account_id: None | str | Unset
        if isinstance(self.account_id, Unset):
            account_id = UNSET
        elif isinstance(self.account_id, UUID):
            account_id = str(self.account_id)
        else:
            account_id = self.account_id

        agent_id: None | str | Unset
        if isinstance(self.agent_id, Unset):
            agent_id = UNSET
        elif isinstance(self.agent_id, UUID):
            agent_id = str(self.agent_id)
        else:
            agent_id = self.agent_id

        agent_name: None | str | Unset
        if isinstance(self.agent_name, Unset):
            agent_name = UNSET
        else:
            agent_name = self.agent_name

        credential_mode: str | Unset = UNSET
        if not isinstance(self.credential_mode, Unset):
            credential_mode = self.credential_mode.value

        reach: dict[str, Any] | None | Unset
        if isinstance(self.reach, Unset):
            reach = UNSET
        elif isinstance(self.reach, SurfaceReach):
            reach = self.reach.to_dict()
        else:
            reach = self.reach

        status: str | Unset = UNSET
        if not isinstance(self.status, Unset):
            status = self.status.value

        surface_identity_email: None | str | Unset
        if isinstance(self.surface_identity_email, Unset):
            surface_identity_email = UNSET
        else:
            surface_identity_email = self.surface_identity_email

        surface_identity_id: None | str | Unset
        if isinstance(self.surface_identity_id, Unset):
            surface_identity_id = UNSET
        else:
            surface_identity_id = self.surface_identity_id

        surface_identity_username: None | str | Unset
        if isinstance(self.surface_identity_username, Unset):
            surface_identity_username = UNSET
        else:
            surface_identity_username = self.surface_identity_username

        uses_default_agent = self.uses_default_agent

        webhook_url: None | str | Unset
        if isinstance(self.webhook_url, Unset):
            webhook_url = UNSET
        else:
            webhook_url = self.webhook_url

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "config": config,
                "id": id,
                "name": name,
                "platform": platform,
                "pod_id": pod_id,
            }
        )
        if account_id is not UNSET:
            field_dict["account_id"] = account_id
        if agent_id is not UNSET:
            field_dict["agent_id"] = agent_id
        if agent_name is not UNSET:
            field_dict["agent_name"] = agent_name
        if credential_mode is not UNSET:
            field_dict["credential_mode"] = credential_mode
        if reach is not UNSET:
            field_dict["reach"] = reach
        if status is not UNSET:
            field_dict["status"] = status
        if surface_identity_email is not UNSET:
            field_dict["surface_identity_email"] = surface_identity_email
        if surface_identity_id is not UNSET:
            field_dict["surface_identity_id"] = surface_identity_id
        if surface_identity_username is not UNSET:
            field_dict["surface_identity_username"] = surface_identity_username
        if uses_default_agent is not UNSET:
            field_dict["uses_default_agent"] = uses_default_agent
        if webhook_url is not UNSET:
            field_dict["webhook_url"] = webhook_url

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.surface_config_response import SurfaceConfigResponse
        from ..models.surface_reach import SurfaceReach

        d = dict(src_dict)
        config = SurfaceConfigResponse.from_dict(d.pop("config"))

        id = UUID(d.pop("id"))

        name = d.pop("name")

        platform = SurfacePlatform(d.pop("platform"))

        pod_id = UUID(d.pop("pod_id"))

        def _parse_account_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                account_id_type_0 = UUID(data)

                return account_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | UUID, data)

        account_id = _parse_account_id(d.pop("account_id", UNSET))

        def _parse_agent_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                agent_id_type_0 = UUID(data)

                return agent_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | UUID, data)

        agent_id = _parse_agent_id(d.pop("agent_id", UNSET))

        def _parse_agent_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        agent_name = _parse_agent_name(d.pop("agent_name", UNSET))

        _credential_mode = d.pop("credential_mode", UNSET)
        credential_mode: SurfaceCredentialMode | Unset
        if isinstance(_credential_mode, Unset):
            credential_mode = UNSET
        else:
            credential_mode = SurfaceCredentialMode(_credential_mode)

        def _parse_reach(data: object) -> None | SurfaceReach | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                reach_type_0 = SurfaceReach.from_dict(data)

                return reach_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | SurfaceReach | Unset, data)

        reach = _parse_reach(d.pop("reach", UNSET))

        _status = d.pop("status", UNSET)
        status: AgentSurfaceStatus | Unset
        if isinstance(_status, Unset):
            status = UNSET
        else:
            status = AgentSurfaceStatus(_status)

        def _parse_surface_identity_email(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        surface_identity_email = _parse_surface_identity_email(
            d.pop("surface_identity_email", UNSET)
        )

        def _parse_surface_identity_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        surface_identity_id = _parse_surface_identity_id(
            d.pop("surface_identity_id", UNSET)
        )

        def _parse_surface_identity_username(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        surface_identity_username = _parse_surface_identity_username(
            d.pop("surface_identity_username", UNSET)
        )

        uses_default_agent = d.pop("uses_default_agent", UNSET)

        def _parse_webhook_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        webhook_url = _parse_webhook_url(d.pop("webhook_url", UNSET))

        agent_surface_response = cls(
            config=config,
            id=id,
            name=name,
            platform=platform,
            pod_id=pod_id,
            account_id=account_id,
            agent_id=agent_id,
            agent_name=agent_name,
            credential_mode=credential_mode,
            reach=reach,
            status=status,
            surface_identity_email=surface_identity_email,
            surface_identity_id=surface_identity_id,
            surface_identity_username=surface_identity_username,
            uses_default_agent=uses_default_agent,
            webhook_url=webhook_url,
        )

        agent_surface_response.additional_properties = d
        return agent_surface_response

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
