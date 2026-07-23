from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.function_status import FunctionStatus
from ..models.function_type import FunctionType
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.function_summary_response_config_type_0 import (
        FunctionSummaryResponseConfigType0,
    )


T = TypeVar("T", bound="FunctionSummaryResponse")


@_attrs_define
class FunctionSummaryResponse:
    """Lean function shape for list responses.

    Omits the heavy `input_schema` / `output_schema` / `config_schema` (full JSON
    schemas derived from the function code) and `code` — fetch those from
    `function.get`.

        Attributes:
            created_at (Any):
            id (UUID):
            name (str):
            pod_id (UUID):
            status (FunctionStatus): Status of a function.
            type_ (FunctionType): Execution mode for a function.
            updated_at (Any):
            user_id (UUID):
            allowed_actions (list[str] | Unset):
            code_hash (None | str | Unset):
            code_path (None | str | Unset):
            config (FunctionSummaryResponseConfigType0 | None | Unset):
            description (None | str | Unset):
            icon_url (None | str | Unset):
            python_packages (list[str] | Unset):
            visibility (str | Unset):  Default: 'POD'.
    """

    created_at: Any
    id: UUID
    name: str
    pod_id: UUID
    status: FunctionStatus
    type_: FunctionType
    updated_at: Any
    user_id: UUID
    allowed_actions: list[str] | Unset = UNSET
    code_hash: None | str | Unset = UNSET
    code_path: None | str | Unset = UNSET
    config: FunctionSummaryResponseConfigType0 | None | Unset = UNSET
    description: None | str | Unset = UNSET
    icon_url: None | str | Unset = UNSET
    python_packages: list[str] | Unset = UNSET
    visibility: str | Unset = "POD"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.function_summary_response_config_type_0 import (
            FunctionSummaryResponseConfigType0,
        )

        created_at = self.created_at

        id = str(self.id)

        name = self.name

        pod_id = str(self.pod_id)

        status = self.status.value

        type_ = self.type_.value

        updated_at = self.updated_at

        user_id = str(self.user_id)

        allowed_actions: list[str] | Unset = UNSET
        if not isinstance(self.allowed_actions, Unset):
            allowed_actions = self.allowed_actions

        code_hash: None | str | Unset
        if isinstance(self.code_hash, Unset):
            code_hash = UNSET
        else:
            code_hash = self.code_hash

        code_path: None | str | Unset
        if isinstance(self.code_path, Unset):
            code_path = UNSET
        else:
            code_path = self.code_path

        config: dict[str, Any] | None | Unset
        if isinstance(self.config, Unset):
            config = UNSET
        elif isinstance(self.config, FunctionSummaryResponseConfigType0):
            config = self.config.to_dict()
        else:
            config = self.config

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        icon_url: None | str | Unset
        if isinstance(self.icon_url, Unset):
            icon_url = UNSET
        else:
            icon_url = self.icon_url

        python_packages: list[str] | Unset = UNSET
        if not isinstance(self.python_packages, Unset):
            python_packages = self.python_packages

        visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "name": name,
                "pod_id": pod_id,
                "status": status,
                "type": type_,
                "updated_at": updated_at,
                "user_id": user_id,
            }
        )
        if allowed_actions is not UNSET:
            field_dict["allowed_actions"] = allowed_actions
        if code_hash is not UNSET:
            field_dict["code_hash"] = code_hash
        if code_path is not UNSET:
            field_dict["code_path"] = code_path
        if config is not UNSET:
            field_dict["config"] = config
        if description is not UNSET:
            field_dict["description"] = description
        if icon_url is not UNSET:
            field_dict["icon_url"] = icon_url
        if python_packages is not UNSET:
            field_dict["python_packages"] = python_packages
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.function_summary_response_config_type_0 import (
            FunctionSummaryResponseConfigType0,
        )

        d = dict(src_dict)
        created_at = d.pop("created_at")

        id = UUID(d.pop("id"))

        name = d.pop("name")

        pod_id = UUID(d.pop("pod_id"))

        status = FunctionStatus(d.pop("status"))

        type_ = FunctionType(d.pop("type"))

        updated_at = d.pop("updated_at")

        user_id = UUID(d.pop("user_id"))

        allowed_actions = cast(list[str], d.pop("allowed_actions", UNSET))

        def _parse_code_hash(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        code_hash = _parse_code_hash(d.pop("code_hash", UNSET))

        def _parse_code_path(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        code_path = _parse_code_path(d.pop("code_path", UNSET))

        def _parse_config(
            data: object,
        ) -> FunctionSummaryResponseConfigType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_type_0 = FunctionSummaryResponseConfigType0.from_dict(data)

                return config_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(FunctionSummaryResponseConfigType0 | None | Unset, data)

        config = _parse_config(d.pop("config", UNSET))

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_icon_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        icon_url = _parse_icon_url(d.pop("icon_url", UNSET))

        python_packages = cast(list[str], d.pop("python_packages", UNSET))

        visibility = d.pop("visibility", UNSET)

        function_summary_response = cls(
            created_at=created_at,
            id=id,
            name=name,
            pod_id=pod_id,
            status=status,
            type_=type_,
            updated_at=updated_at,
            user_id=user_id,
            allowed_actions=allowed_actions,
            code_hash=code_hash,
            code_path=code_path,
            config=config,
            description=description,
            icon_url=icon_url,
            python_packages=python_packages,
            visibility=visibility,
        )

        function_summary_response.additional_properties = d
        return function_summary_response

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
