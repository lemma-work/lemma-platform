from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="DecisionImageBody")


@_attrs_define
class DecisionImageBody:
    """
    Attributes:
        path (str): A file of this Lemma, by path (`/receipts/0042.jpg`, or `/me/...` for one of your own). Read with
            your access: a file you cannot read is refused (422).
    """

    path: str

    def to_dict(self) -> dict[str, Any]:
        path = self.path

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "path": path,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        path = d.pop("path")

        decision_image_body = cls(
            path=path,
        )

        return decision_image_body
