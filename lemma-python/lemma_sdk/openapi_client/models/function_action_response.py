from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.function_status import FunctionStatus
from ..models.function_type import FunctionType
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.json_object import JsonObject


T = TypeVar("T", bound="FunctionActionResponse")


@_attrs_define
class FunctionActionResponse:
    """
    Attributes:
        created_at (datetime.datetime | None):
        id (UUID):
        input_schema (JsonObject):
        name (str):
        output_schema (JsonObject):
        pod_id (UUID):
        status (FunctionStatus): Status of a function.
        type_ (FunctionType): Execution mode for a function.
        updated_at (datetime.datetime | None):
        user_id (UUID):
        allowed_actions (list[str] | Unset):
        code (None | str | Unset):
        code_path (None | str | Unset):
        config (JsonObject | None | Unset):
        config_schema (JsonObject | None | Unset): Optional configuration schema derived from the function code.
        description (None | str | Unset):
        icon_url (None | str | Unset):
        revision_hash (None | str | Unset):
        visibility (str | Unset):  Default: 'POD'.
    """

    created_at: datetime.datetime | None
    id: UUID
    input_schema: JsonObject
    name: str
    output_schema: JsonObject
    pod_id: UUID
    status: FunctionStatus
    type_: FunctionType
    updated_at: datetime.datetime | None
    user_id: UUID
    allowed_actions: list[str] | Unset = UNSET
    code: None | str | Unset = UNSET
    code_path: None | str | Unset = UNSET
    config: JsonObject | None | Unset = UNSET
    config_schema: JsonObject | None | Unset = UNSET
    description: None | str | Unset = UNSET
    icon_url: None | str | Unset = UNSET
    revision_hash: None | str | Unset = UNSET
    visibility: str | Unset = "POD"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.json_object import JsonObject

        created_at: None | str
        if isinstance(self.created_at, datetime.datetime):
            created_at = self.created_at.isoformat()
        else:
            created_at = self.created_at

        id = str(self.id)

        input_schema = self.input_schema.to_dict()

        name = self.name

        output_schema = self.output_schema.to_dict()

        pod_id = str(self.pod_id)

        status = self.status.value

        type_ = self.type_.value

        updated_at: None | str
        if isinstance(self.updated_at, datetime.datetime):
            updated_at = self.updated_at.isoformat()
        else:
            updated_at = self.updated_at

        user_id = str(self.user_id)

        allowed_actions: list[str] | Unset = UNSET
        if not isinstance(self.allowed_actions, Unset):
            allowed_actions = self.allowed_actions

        code: None | str | Unset
        if isinstance(self.code, Unset):
            code = UNSET
        else:
            code = self.code

        code_path: None | str | Unset
        if isinstance(self.code_path, Unset):
            code_path = UNSET
        else:
            code_path = self.code_path

        config: dict[str, Any] | None | Unset
        if isinstance(self.config, Unset):
            config = UNSET
        elif isinstance(self.config, JsonObject):
            config = self.config.to_dict()
        else:
            config = self.config

        config_schema: dict[str, Any] | None | Unset
        if isinstance(self.config_schema, Unset):
            config_schema = UNSET
        elif isinstance(self.config_schema, JsonObject):
            config_schema = self.config_schema.to_dict()
        else:
            config_schema = self.config_schema

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

        revision_hash: None | str | Unset
        if isinstance(self.revision_hash, Unset):
            revision_hash = UNSET
        else:
            revision_hash = self.revision_hash

        visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "input_schema": input_schema,
                "name": name,
                "output_schema": output_schema,
                "pod_id": pod_id,
                "status": status,
                "type": type_,
                "updated_at": updated_at,
                "user_id": user_id,
            }
        )
        if allowed_actions is not UNSET:
            field_dict["allowed_actions"] = allowed_actions
        if code is not UNSET:
            field_dict["code"] = code
        if code_path is not UNSET:
            field_dict["code_path"] = code_path
        if config is not UNSET:
            field_dict["config"] = config
        if config_schema is not UNSET:
            field_dict["config_schema"] = config_schema
        if description is not UNSET:
            field_dict["description"] = description
        if icon_url is not UNSET:
            field_dict["icon_url"] = icon_url
        if revision_hash is not UNSET:
            field_dict["revision_hash"] = revision_hash
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.json_object import JsonObject

        d = dict(src_dict)

        def _parse_created_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                created_at_type_0 = isoparse(data)

                return created_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None, data)

        created_at = _parse_created_at(d.pop("created_at"))

        id = UUID(d.pop("id"))

        input_schema = JsonObject.from_dict(d.pop("input_schema"))

        name = d.pop("name")

        output_schema = JsonObject.from_dict(d.pop("output_schema"))

        pod_id = UUID(d.pop("pod_id"))

        status = FunctionStatus(d.pop("status"))

        type_ = FunctionType(d.pop("type"))

        def _parse_updated_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                updated_at_type_0 = isoparse(data)

                return updated_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None, data)

        updated_at = _parse_updated_at(d.pop("updated_at"))

        user_id = UUID(d.pop("user_id"))

        allowed_actions = cast(list[str], d.pop("allowed_actions", UNSET))

        def _parse_code(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        code = _parse_code(d.pop("code", UNSET))

        def _parse_code_path(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        code_path = _parse_code_path(d.pop("code_path", UNSET))

        def _parse_config(data: object) -> JsonObject | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_type_0 = JsonObject.from_dict(data)

                return config_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(JsonObject | None | Unset, data)

        config = _parse_config(d.pop("config", UNSET))

        def _parse_config_schema(data: object) -> JsonObject | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_schema_type_0 = JsonObject.from_dict(data)

                return config_schema_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(JsonObject | None | Unset, data)

        config_schema = _parse_config_schema(d.pop("config_schema", UNSET))

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

        def _parse_revision_hash(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        revision_hash = _parse_revision_hash(d.pop("revision_hash", UNSET))

        visibility = d.pop("visibility", UNSET)

        function_action_response = cls(
            created_at=created_at,
            id=id,
            input_schema=input_schema,
            name=name,
            output_schema=output_schema,
            pod_id=pod_id,
            status=status,
            type_=type_,
            updated_at=updated_at,
            user_id=user_id,
            allowed_actions=allowed_actions,
            code=code,
            code_path=code_path,
            config=config,
            config_schema=config_schema,
            description=description,
            icon_url=icon_url,
            revision_hash=revision_hash,
            visibility=visibility,
        )

        function_action_response.additional_properties = d
        return function_action_response

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
