from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.function_type import FunctionType
from ..models.resource_visibility import ResourceVisibility
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.create_function_request_config_type_0 import (
        CreateFunctionRequestConfigType0,
    )


T = TypeVar("T", bound="CreateFunctionRequest")


@_attrs_define
class CreateFunctionRequest:
    """Request to create a function.

    Input and output schemas are derived from the submitted code and returned
    on the function response. They are not accepted in create requests.

        Attributes:
            name (str):
            code (None | str | Unset): Python source for the function. When provided, the platform analyzes the code and
                populates input_schema, output_schema, and config_schema on the returned function.
            config (CreateFunctionRequestConfigType0 | None | Unset):
            description (None | str | Unset):
            icon_url (None | str | Unset):
            type_ (FunctionType | Unset): Execution mode for a function.
            visibility (ResourceVisibility | Unset):
    """

    name: str
    code: None | str | Unset = UNSET
    config: CreateFunctionRequestConfigType0 | None | Unset = UNSET
    description: None | str | Unset = UNSET
    icon_url: None | str | Unset = UNSET
    type_: FunctionType | Unset = UNSET
    visibility: ResourceVisibility | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.create_function_request_config_type_0 import (
            CreateFunctionRequestConfigType0,
        )

        name = self.name

        code: None | str | Unset
        if isinstance(self.code, Unset):
            code = UNSET
        else:
            code = self.code

        config: dict[str, Any] | None | Unset
        if isinstance(self.config, Unset):
            config = UNSET
        elif isinstance(self.config, CreateFunctionRequestConfigType0):
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

        type_: str | Unset = UNSET
        if not isinstance(self.type_, Unset):
            type_ = self.type_.value

        visibility: str | Unset = UNSET
        if not isinstance(self.visibility, Unset):
            visibility = self.visibility.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "name": name,
            }
        )
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
        from ..models.create_function_request_config_type_0 import (
            CreateFunctionRequestConfigType0,
        )

        d = dict(src_dict)
        name = d.pop("name")

        def _parse_code(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        code = _parse_code(d.pop("code", UNSET))

        def _parse_config(
            data: object,
        ) -> CreateFunctionRequestConfigType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_type_0 = CreateFunctionRequestConfigType0.from_dict(data)

                return config_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(CreateFunctionRequestConfigType0 | None | Unset, data)

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

        _type_ = d.pop("type", UNSET)
        type_: FunctionType | Unset
        if isinstance(_type_, Unset):
            type_ = UNSET
        else:
            type_ = FunctionType(_type_)

        _visibility = d.pop("visibility", UNSET)
        visibility: ResourceVisibility | Unset
        if isinstance(_visibility, Unset):
            visibility = UNSET
        else:
            visibility = ResourceVisibility(_visibility)

        create_function_request = cls(
            name=name,
            code=code,
            config=config,
            description=description,
            icon_url=icon_url,
            type_=type_,
            visibility=visibility,
        )

        return create_function_request
