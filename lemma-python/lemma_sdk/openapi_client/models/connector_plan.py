from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.connector_plan_mode import ConnectorPlanMode
from ..models.connector_plan_status import ConnectorPlanStatus
from ..models.connector_plan_visibility import ConnectorPlanVisibility
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.account_option import AccountOption
    from ..models.installation_option import InstallationOption


T = TypeVar("T", bound="ConnectorPlan")


@_attrs_define
class ConnectorPlan:
    """
    Attributes:
        connector (str):
        mode (ConnectorPlanMode):
        slug (str):
        status (ConnectorPlanStatus):
        account_options (list[AccountOption] | Unset):
        auth_config_id (None | Unset | UUID):
        can_create_installation (bool | Unset):  Default: False.
        installations (list[InstallationOption] | Unset):
        message (None | str | Unset):
        purpose (None | str | Unset):
        required (bool | Unset):  Default: False.
        used_by (list[str] | Unset):
        visibility (ConnectorPlanVisibility | Unset):  Default: ConnectorPlanVisibility.POD.
    """

    connector: str
    mode: ConnectorPlanMode
    slug: str
    status: ConnectorPlanStatus
    account_options: list[AccountOption] | Unset = UNSET
    auth_config_id: None | Unset | UUID = UNSET
    can_create_installation: bool | Unset = False
    installations: list[InstallationOption] | Unset = UNSET
    message: None | str | Unset = UNSET
    purpose: None | str | Unset = UNSET
    required: bool | Unset = False
    used_by: list[str] | Unset = UNSET
    visibility: ConnectorPlanVisibility | Unset = ConnectorPlanVisibility.POD
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        connector = self.connector

        mode = self.mode.value

        slug = self.slug

        status = self.status.value

        account_options: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.account_options, Unset):
            account_options = []
            for account_options_item_data in self.account_options:
                account_options_item = account_options_item_data.to_dict()
                account_options.append(account_options_item)

        auth_config_id: None | str | Unset
        if isinstance(self.auth_config_id, Unset):
            auth_config_id = UNSET
        elif isinstance(self.auth_config_id, UUID):
            auth_config_id = str(self.auth_config_id)
        else:
            auth_config_id = self.auth_config_id

        can_create_installation = self.can_create_installation

        installations: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.installations, Unset):
            installations = []
            for installations_item_data in self.installations:
                installations_item = installations_item_data.to_dict()
                installations.append(installations_item)

        message: None | str | Unset
        if isinstance(self.message, Unset):
            message = UNSET
        else:
            message = self.message

        purpose: None | str | Unset
        if isinstance(self.purpose, Unset):
            purpose = UNSET
        else:
            purpose = self.purpose

        required = self.required

        used_by: list[str] | Unset = UNSET
        if not isinstance(self.used_by, Unset):
            used_by = self.used_by

        visibility: str | Unset = UNSET
        if not isinstance(self.visibility, Unset):
            visibility = self.visibility.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "connector": connector,
                "mode": mode,
                "slug": slug,
                "status": status,
            }
        )
        if account_options is not UNSET:
            field_dict["account_options"] = account_options
        if auth_config_id is not UNSET:
            field_dict["auth_config_id"] = auth_config_id
        if can_create_installation is not UNSET:
            field_dict["can_create_installation"] = can_create_installation
        if installations is not UNSET:
            field_dict["installations"] = installations
        if message is not UNSET:
            field_dict["message"] = message
        if purpose is not UNSET:
            field_dict["purpose"] = purpose
        if required is not UNSET:
            field_dict["required"] = required
        if used_by is not UNSET:
            field_dict["used_by"] = used_by
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.account_option import AccountOption
        from ..models.installation_option import InstallationOption

        d = dict(src_dict)
        connector = d.pop("connector")

        mode = ConnectorPlanMode(d.pop("mode"))

        slug = d.pop("slug")

        status = ConnectorPlanStatus(d.pop("status"))

        _account_options = d.pop("account_options", UNSET)
        account_options: list[AccountOption] | Unset = UNSET
        if _account_options is not UNSET:
            account_options = []
            for account_options_item_data in _account_options:
                account_options_item = AccountOption.from_dict(
                    account_options_item_data
                )

                account_options.append(account_options_item)

        def _parse_auth_config_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                auth_config_id_type_0 = UUID(data)

                return auth_config_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        auth_config_id = _parse_auth_config_id(d.pop("auth_config_id", UNSET))

        can_create_installation = d.pop("can_create_installation", UNSET)

        _installations = d.pop("installations", UNSET)
        installations: list[InstallationOption] | Unset = UNSET
        if _installations is not UNSET:
            installations = []
            for installations_item_data in _installations:
                installations_item = InstallationOption.from_dict(
                    installations_item_data
                )

                installations.append(installations_item)

        def _parse_message(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        message = _parse_message(d.pop("message", UNSET))

        def _parse_purpose(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        purpose = _parse_purpose(d.pop("purpose", UNSET))

        required = d.pop("required", UNSET)

        used_by = cast(list[str], d.pop("used_by", UNSET))

        _visibility = d.pop("visibility", UNSET)
        visibility: ConnectorPlanVisibility | Unset
        if isinstance(_visibility, Unset):
            visibility = UNSET
        else:
            visibility = ConnectorPlanVisibility(_visibility)

        connector_plan = cls(
            connector=connector,
            mode=mode,
            slug=slug,
            status=status,
            account_options=account_options,
            auth_config_id=auth_config_id,
            can_create_installation=can_create_installation,
            installations=installations,
            message=message,
            purpose=purpose,
            required=required,
            used_by=used_by,
            visibility=visibility,
        )

        connector_plan.additional_properties = d
        return connector_plan

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
