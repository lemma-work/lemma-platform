from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="VerifyTokenResponse")


@_attrs_define
class VerifyTokenResponse:
    """
    Attributes:
        email (str):
        user_id (UUID):
        function_id (None | Unset | UUID):
        function_name (None | str | Unset):
        organization_id (None | Unset | UUID):
        pod_id (None | Unset | UUID):
        scopes (list[str] | Unset):
    """

    email: str
    user_id: UUID
    function_id: None | Unset | UUID = UNSET
    function_name: None | str | Unset = UNSET
    organization_id: None | Unset | UUID = UNSET
    pod_id: None | Unset | UUID = UNSET
    scopes: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        email = self.email

        user_id = str(self.user_id)

        function_id: None | str | Unset
        if isinstance(self.function_id, Unset):
            function_id = UNSET
        elif isinstance(self.function_id, UUID):
            function_id = str(self.function_id)
        else:
            function_id = self.function_id

        function_name: None | str | Unset
        if isinstance(self.function_name, Unset):
            function_name = UNSET
        else:
            function_name = self.function_name

        organization_id: None | str | Unset
        if isinstance(self.organization_id, Unset):
            organization_id = UNSET
        elif isinstance(self.organization_id, UUID):
            organization_id = str(self.organization_id)
        else:
            organization_id = self.organization_id

        pod_id: None | str | Unset
        if isinstance(self.pod_id, Unset):
            pod_id = UNSET
        elif isinstance(self.pod_id, UUID):
            pod_id = str(self.pod_id)
        else:
            pod_id = self.pod_id

        scopes: list[str] | Unset = UNSET
        if not isinstance(self.scopes, Unset):
            scopes = self.scopes

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "email": email,
                "user_id": user_id,
            }
        )
        if function_id is not UNSET:
            field_dict["function_id"] = function_id
        if function_name is not UNSET:
            field_dict["function_name"] = function_name
        if organization_id is not UNSET:
            field_dict["organization_id"] = organization_id
        if pod_id is not UNSET:
            field_dict["pod_id"] = pod_id
        if scopes is not UNSET:
            field_dict["scopes"] = scopes

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        email = d.pop("email")

        user_id = UUID(d.pop("user_id"))

        def _parse_function_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                function_id_type_0 = UUID(data)

                return function_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        function_id = _parse_function_id(d.pop("function_id", UNSET))

        def _parse_function_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        function_name = _parse_function_name(d.pop("function_name", UNSET))

        def _parse_organization_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                organization_id_type_0 = UUID(data)

                return organization_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        organization_id = _parse_organization_id(d.pop("organization_id", UNSET))

        def _parse_pod_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                pod_id_type_0 = UUID(data)

                return pod_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        pod_id = _parse_pod_id(d.pop("pod_id", UNSET))

        scopes = cast(list[str], d.pop("scopes", UNSET))

        verify_token_response = cls(
            email=email,
            user_id=user_id,
            function_id=function_id,
            function_name=function_name,
            organization_id=organization_id,
            pod_id=pod_id,
            scopes=scopes,
        )

        verify_token_response.additional_properties = d
        return verify_token_response

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
