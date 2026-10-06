from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.widget_answer import WidgetAnswer
from ..types import UNSET, Unset

T = TypeVar("T", bound="WebWidgetCreateRequest")


@_attrs_define
class WebWidgetCreateRequest:
    """
    Attributes:
        name (str):
        agent_name (None | str | Unset): The agent that answers. The pod's assistant if omitted.
        allowed_origins (list[str] | Unset):
        answer (WidgetAnswer | Unset): Whom a widget answers. Mirrors a bot's ``contacts.answer``.
        looked_after_by (None | Unset | UUID):
    """

    name: str
    agent_name: None | str | Unset = UNSET
    allowed_origins: list[str] | Unset = UNSET
    answer: WidgetAnswer | Unset = UNSET
    looked_after_by: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        agent_name: None | str | Unset
        if isinstance(self.agent_name, Unset):
            agent_name = UNSET
        else:
            agent_name = self.agent_name

        allowed_origins: list[str] | Unset = UNSET
        if not isinstance(self.allowed_origins, Unset):
            allowed_origins = self.allowed_origins

        answer: str | Unset = UNSET
        if not isinstance(self.answer, Unset):
            answer = self.answer.value

        looked_after_by: None | str | Unset
        if isinstance(self.looked_after_by, Unset):
            looked_after_by = UNSET
        elif isinstance(self.looked_after_by, UUID):
            looked_after_by = str(self.looked_after_by)
        else:
            looked_after_by = self.looked_after_by

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
            }
        )
        if agent_name is not UNSET:
            field_dict["agent_name"] = agent_name
        if allowed_origins is not UNSET:
            field_dict["allowed_origins"] = allowed_origins
        if answer is not UNSET:
            field_dict["answer"] = answer
        if looked_after_by is not UNSET:
            field_dict["looked_after_by"] = looked_after_by

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        def _parse_agent_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        agent_name = _parse_agent_name(d.pop("agent_name", UNSET))

        allowed_origins = cast(list[str], d.pop("allowed_origins", UNSET))

        _answer = d.pop("answer", UNSET)
        answer: WidgetAnswer | Unset
        if isinstance(_answer, Unset):
            answer = UNSET
        else:
            answer = WidgetAnswer(_answer)

        def _parse_looked_after_by(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                looked_after_by_type_0 = UUID(data)

                return looked_after_by_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        looked_after_by = _parse_looked_after_by(d.pop("looked_after_by", UNSET))

        web_widget_create_request = cls(
            name=name,
            agent_name=agent_name,
            allowed_origins=allowed_origins,
            answer=answer,
            looked_after_by=looked_after_by,
        )

        web_widget_create_request.additional_properties = d
        return web_widget_create_request

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
