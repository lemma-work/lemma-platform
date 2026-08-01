from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="NotifyNodeConfig")


@_attrs_define
class NotifyNodeConfig:
    """Who to tell, and what.

    Distinct from a FORM node: a form *blocks* the run until somebody answers,
    which is right when the run needs their input and wrong when it merely needs
    them informed. This node never suspends.

        Attributes:
            message (str): What to say. Supports the same expression interpolation as other node inputs, so it can carry
                values from earlier steps.
            recipient_user_id (None | Unset | UUID): Pod member to notify.
            recipient_user_id_expression (None | str | Unset): Optional JMESPath expression resolving to a pod member id.
                Takes precedence over recipient_user_id.
            title (None | str | Unset): Optional short subject line for the inbox.
    """

    message: str
    recipient_user_id: None | Unset | UUID = UNSET
    recipient_user_id_expression: None | str | Unset = UNSET
    title: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        message = self.message

        recipient_user_id: None | str | Unset
        if isinstance(self.recipient_user_id, Unset):
            recipient_user_id = UNSET
        elif isinstance(self.recipient_user_id, UUID):
            recipient_user_id = str(self.recipient_user_id)
        else:
            recipient_user_id = self.recipient_user_id

        recipient_user_id_expression: None | str | Unset
        if isinstance(self.recipient_user_id_expression, Unset):
            recipient_user_id_expression = UNSET
        else:
            recipient_user_id_expression = self.recipient_user_id_expression

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "message": message,
            }
        )
        if recipient_user_id is not UNSET:
            field_dict["recipient_user_id"] = recipient_user_id
        if recipient_user_id_expression is not UNSET:
            field_dict["recipient_user_id_expression"] = recipient_user_id_expression
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        message = d.pop("message")

        def _parse_recipient_user_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                recipient_user_id_type_0 = UUID(data)

                return recipient_user_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        recipient_user_id = _parse_recipient_user_id(d.pop("recipient_user_id", UNSET))

        def _parse_recipient_user_id_expression(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        recipient_user_id_expression = _parse_recipient_user_id_expression(
            d.pop("recipient_user_id_expression", UNSET)
        )

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        notify_node_config = cls(
            message=message,
            recipient_user_id=recipient_user_id,
            recipient_user_id_expression=recipient_user_id_expression,
            title=title,
        )

        notify_node_config.additional_properties = d
        return notify_node_config

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
