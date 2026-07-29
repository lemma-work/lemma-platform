from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.function_type import FunctionType
from ..models.resource_visibility import ResourceVisibility
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.json_object import JsonObject


T = TypeVar("T", bound="UpdateFunctionRequest")


@_attrs_define
class UpdateFunctionRequest:
    """Request to update a function.

    Attributes:
        code (None | str | Unset): Updated Python source for the function. When provided, the platform re-analyzes the
            code and refreshes input_schema, output_schema, and config_schema on the returned function.
        config (JsonObject | None | Unset):
        description (None | str | Unset):
        icon_url (None | str | Unset):
        type_ (FunctionType | None | Unset):
        visibility (None | ResourceVisibility | Unset):
    """

    code: None | str | Unset = UNSET
    config: JsonObject | None | Unset = UNSET
    description: None | str | Unset = UNSET
    icon_url: None | str | Unset = UNSET
    type_: FunctionType | None | Unset = UNSET
    visibility: None | ResourceVisibility | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.json_object import JsonObject

        code: None | str | Unset
        if isinstance(self.code, Unset):
            code = UNSET
        else:
            code = self.code

        config: dict[str, Any] | None | Unset
        if isinstance(self.config, Unset):
            config = UNSET
        elif isinstance(self.config, JsonObject):
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

        type_: None | str | Unset
        if isinstance(self.type_, Unset):
            type_ = UNSET
        elif isinstance(self.type_, FunctionType):
            type_ = self.type_.value
        else:
            type_ = self.type_

        visibility: None | str | Unset
        if isinstance(self.visibility, Unset):
            visibility = UNSET
        elif isinstance(self.visibility, ResourceVisibility):
            visibility = self.visibility.value
        else:
            visibility = self.visibility

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if code is not UNSET:
            field_dict["code"] = code
        if config is not UNSET:
            field_dict["config"] = config
        if description is not UNSET:
            field_dict["description"] = description
        if icon_url is not UNSET:
            field_dict["icon_url"] = icon_url
        if type_ is not UNSET:
            field_dict["type"] = type_
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.json_object import JsonObject

        d = dict(src_dict)

        def _parse_code(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        code = _parse_code(d.pop("code", UNSET))

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

        def _parse_type_(data: object) -> FunctionType | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                type_type_0 = FunctionType(data)

                return type_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(FunctionType | None | Unset, data)

        type_ = _parse_type_(d.pop("type", UNSET))

        def _parse_visibility(data: object) -> None | ResourceVisibility | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                visibility_type_0 = ResourceVisibility(data)

                return visibility_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | ResourceVisibility | Unset, data)

        visibility = _parse_visibility(d.pop("visibility", UNSET))

        update_function_request = cls(
            code=code,
            config=config,
            description=description,
            icon_url=icon_url,
            type_=type_,
            visibility=visibility,
        )

        return update_function_request
