from __future__ import annotations

from collections.abc import Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    TypeVar,
    cast,
)

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.notify_node_config import NotifyNodeConfig
    from ..models.notify_node_response_position_type_0 import (
        NotifyNodeResponsePositionType0,
    )


T = TypeVar("T", bound="NotifyNodeResponse")


@_attrs_define
class NotifyNodeResponse:
    """
    Attributes:
        config (NotifyNodeConfig): Who to tell, and what.

            Distinct from a FORM node: a form *blocks* the run until somebody answers,
            which is right when the run needs their input and wrong when it merely needs
            them informed. This node never suspends.
        id (str):
        label (None | str | Unset):
        position (None | NotifyNodeResponsePositionType0 | Unset):
        type_ (Literal['NOTIFY'] | Unset):  Default: 'NOTIFY'.
    """

    config: NotifyNodeConfig
    id: str
    label: None | str | Unset = UNSET
    position: None | NotifyNodeResponsePositionType0 | Unset = UNSET
    type_: Literal["NOTIFY"] | Unset = "NOTIFY"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.notify_node_response_position_type_0 import (
            NotifyNodeResponsePositionType0,
        )

        config = self.config.to_dict()

        id = self.id

        label: None | str | Unset
        if isinstance(self.label, Unset):
            label = UNSET
        else:
            label = self.label

        position: dict[str, Any] | None | Unset
        if isinstance(self.position, Unset):
            position = UNSET
        elif isinstance(self.position, NotifyNodeResponsePositionType0):
            position = self.position.to_dict()
        else:
            position = self.position

        type_ = self.type_

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "config": config,
                "id": id,
            }
        )
        if label is not UNSET:
            field_dict["label"] = label
        if position is not UNSET:
            field_dict["position"] = position
        if type_ is not UNSET:
            field_dict["type"] = type_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.notify_node_config import NotifyNodeConfig
        from ..models.notify_node_response_position_type_0 import (
            NotifyNodeResponsePositionType0,
        )

        d = dict(src_dict)
        config = NotifyNodeConfig.from_dict(d.pop("config"))

        id = d.pop("id")

        def _parse_label(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        label = _parse_label(d.pop("label", UNSET))

        def _parse_position(
            data: object,
        ) -> None | NotifyNodeResponsePositionType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                position_type_0 = NotifyNodeResponsePositionType0.from_dict(data)

                return position_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | NotifyNodeResponsePositionType0 | Unset, data)

        position = _parse_position(d.pop("position", UNSET))

        type_ = cast(Literal["NOTIFY"] | Unset, d.pop("type", UNSET))
        if type_ != "NOTIFY" and not isinstance(type_, Unset):
            raise ValueError(f"type must match const 'NOTIFY', got '{type_}'")

        notify_node_response = cls(
            config=config,
            id=id,
            label=label,
            position=position,
            type_=type_,
        )

        notify_node_response.additional_properties = d
        return notify_node_response

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
