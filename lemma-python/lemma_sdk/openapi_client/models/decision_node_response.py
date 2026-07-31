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
    from ..models.decision_node_config import DecisionNodeConfig
    from ..models.decision_node_response_position_type_0 import (
        DecisionNodeResponsePositionType0,
    )


T = TypeVar("T", bound="DecisionNodeResponse")


@_attrs_define
class DecisionNodeResponse:
    """
    Attributes:
        config (DecisionNodeConfig): Configuration for Decision node.
        id (str):
        label (None | str | Unset):
        position (DecisionNodeResponsePositionType0 | None | Unset):
        type_ (Literal['DECISION'] | Unset):  Default: 'DECISION'.
    """

    config: DecisionNodeConfig
    id: str
    label: None | str | Unset = UNSET
    position: DecisionNodeResponsePositionType0 | None | Unset = UNSET
    type_: Literal["DECISION"] | Unset = "DECISION"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.decision_node_response_position_type_0 import (
            DecisionNodeResponsePositionType0,
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
        elif isinstance(self.position, DecisionNodeResponsePositionType0):
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
        from ..models.decision_node_config import DecisionNodeConfig
        from ..models.decision_node_response_position_type_0 import (
            DecisionNodeResponsePositionType0,
        )

        d = dict(src_dict)
        config = DecisionNodeConfig.from_dict(d.pop("config"))

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
        ) -> DecisionNodeResponsePositionType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                position_type_0 = DecisionNodeResponsePositionType0.from_dict(data)

                return position_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(DecisionNodeResponsePositionType0 | None | Unset, data)

        position = _parse_position(d.pop("position", UNSET))

        type_ = cast(Literal["DECISION"] | Unset, d.pop("type", UNSET))
        if type_ != "DECISION" and not isinstance(type_, Unset):
            raise ValueError(f"type must match const 'DECISION', got '{type_}'")

        decision_node_response = cls(
            config=config,
            id=id,
            label=label,
            position=position,
            type_=type_,
        )

        decision_node_response.additional_properties = d
        return decision_node_response

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
