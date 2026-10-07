from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.template_need_response import TemplateNeedResponse


T = TypeVar("T", bound="TemplateWinResponse")


@_attrs_define
class TemplateWinResponse:
    """
    Attributes:
        say (str):
        needs (None | TemplateNeedResponse | Unset):
    """

    say: str
    needs: None | TemplateNeedResponse | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.template_need_response import TemplateNeedResponse

        say = self.say

        needs: dict[str, Any] | None | Unset
        if isinstance(self.needs, Unset):
            needs = UNSET
        elif isinstance(self.needs, TemplateNeedResponse):
            needs = self.needs.to_dict()
        else:
            needs = self.needs

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "say": say,
            }
        )
        if needs is not UNSET:
            field_dict["needs"] = needs

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.template_need_response import TemplateNeedResponse

        d = dict(src_dict)
        say = d.pop("say")

        def _parse_needs(data: object) -> None | TemplateNeedResponse | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                needs_type_0 = TemplateNeedResponse.from_dict(data)

                return needs_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | TemplateNeedResponse | Unset, data)

        needs = _parse_needs(d.pop("needs", UNSET))

        template_win_response = cls(
            say=say,
            needs=needs,
        )

        template_win_response.additional_properties = d
        return template_win_response

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
