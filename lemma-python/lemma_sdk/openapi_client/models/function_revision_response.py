from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.json_object import JsonObject


T = TypeVar("T", bound="FunctionRevisionResponse")


@_attrs_define
class FunctionRevisionResponse:
    """One entry in a function's revision history.

    Attributes:
        created_at (datetime.datetime | None):
        function_id (UUID):
        id (UUID):
        is_live (bool): True for the revision this function runs.
        revision_hash (str):
        revision_number (int):
        code (None | str | Unset):
        config_schema (JsonObject | None | Unset):
        created_by (None | Unset | UUID):
        input_schema (JsonObject | None | Unset):
        label (None | str | Unset):
        output_schema (JsonObject | None | Unset):
        pruned_at (datetime.datetime | None | Unset): Set when retention removed this revision's artifact. The entry
            stays in the history, but it can no longer be run or promoted.
    """

    created_at: datetime.datetime | None
    function_id: UUID
    id: UUID
    is_live: bool
    revision_hash: str
    revision_number: int
    code: None | str | Unset = UNSET
    config_schema: JsonObject | None | Unset = UNSET
    created_by: None | Unset | UUID = UNSET
    input_schema: JsonObject | None | Unset = UNSET
    label: None | str | Unset = UNSET
    output_schema: JsonObject | None | Unset = UNSET
    pruned_at: datetime.datetime | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.json_object import JsonObject

        created_at: None | str
        if isinstance(self.created_at, datetime.datetime):
            created_at = self.created_at.isoformat()
        else:
            created_at = self.created_at

        function_id = str(self.function_id)

        id = str(self.id)

        is_live = self.is_live

        revision_hash = self.revision_hash

        revision_number = self.revision_number

        code: None | str | Unset
        if isinstance(self.code, Unset):
            code = UNSET
        else:
            code = self.code

        config_schema: dict[str, Any] | None | Unset
        if isinstance(self.config_schema, Unset):
            config_schema = UNSET
        elif isinstance(self.config_schema, JsonObject):
            config_schema = self.config_schema.to_dict()
        else:
            config_schema = self.config_schema

        created_by: None | str | Unset
        if isinstance(self.created_by, Unset):
            created_by = UNSET
        elif isinstance(self.created_by, UUID):
            created_by = str(self.created_by)
        else:
            created_by = self.created_by

        input_schema: dict[str, Any] | None | Unset
        if isinstance(self.input_schema, Unset):
            input_schema = UNSET
        elif isinstance(self.input_schema, JsonObject):
            input_schema = self.input_schema.to_dict()
        else:
            input_schema = self.input_schema

        label: None | str | Unset
        if isinstance(self.label, Unset):
            label = UNSET
        else:
            label = self.label

        output_schema: dict[str, Any] | None | Unset
        if isinstance(self.output_schema, Unset):
            output_schema = UNSET
        elif isinstance(self.output_schema, JsonObject):
            output_schema = self.output_schema.to_dict()
        else:
            output_schema = self.output_schema

        pruned_at: None | str | Unset
        if isinstance(self.pruned_at, Unset):
            pruned_at = UNSET
        elif isinstance(self.pruned_at, datetime.datetime):
            pruned_at = self.pruned_at.isoformat()
        else:
            pruned_at = self.pruned_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "function_id": function_id,
                "id": id,
                "is_live": is_live,
                "revision_hash": revision_hash,
                "revision_number": revision_number,
            }
        )
        if code is not UNSET:
            field_dict["code"] = code
        if config_schema is not UNSET:
            field_dict["config_schema"] = config_schema
        if created_by is not UNSET:
            field_dict["created_by"] = created_by
        if input_schema is not UNSET:
            field_dict["input_schema"] = input_schema
        if label is not UNSET:
            field_dict["label"] = label
        if output_schema is not UNSET:
            field_dict["output_schema"] = output_schema
        if pruned_at is not UNSET:
            field_dict["pruned_at"] = pruned_at

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

        function_id = UUID(d.pop("function_id"))

        id = UUID(d.pop("id"))

        is_live = d.pop("is_live")

        revision_hash = d.pop("revision_hash")

        revision_number = d.pop("revision_number")

        def _parse_code(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        code = _parse_code(d.pop("code", UNSET))

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

        def _parse_created_by(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                created_by_type_0 = UUID(data)

                return created_by_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        created_by = _parse_created_by(d.pop("created_by", UNSET))

        def _parse_input_schema(data: object) -> JsonObject | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                input_schema_type_0 = JsonObject.from_dict(data)

                return input_schema_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(JsonObject | None | Unset, data)

        input_schema = _parse_input_schema(d.pop("input_schema", UNSET))

        def _parse_label(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        label = _parse_label(d.pop("label", UNSET))

        def _parse_output_schema(data: object) -> JsonObject | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                output_schema_type_0 = JsonObject.from_dict(data)

                return output_schema_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(JsonObject | None | Unset, data)

        output_schema = _parse_output_schema(d.pop("output_schema", UNSET))

        def _parse_pruned_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                pruned_at_type_0 = isoparse(data)

                return pruned_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        pruned_at = _parse_pruned_at(d.pop("pruned_at", UNSET))

        function_revision_response = cls(
            created_at=created_at,
            function_id=function_id,
            id=id,
            is_live=is_live,
            revision_hash=revision_hash,
            revision_number=revision_number,
            code=code,
            config_schema=config_schema,
            created_by=created_by,
            input_schema=input_schema,
            label=label,
            output_schema=output_schema,
            pruned_at=pruned_at,
        )

        function_revision_response.additional_properties = d
        return function_revision_response

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
