from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.person_ref import PersonRef


T = TypeVar("T", bound="LinePreview")


@_attrs_define
class LinePreview:
    """
    Attributes:
        created_at (datetime.datetime):
        id (UUID):
        sender (PersonRef): Somebody in a channel, said one way everywhere: a member of the pod, one
            of its contacts, or one of its agents. There is nobody else -- everybody
            outside the pod is a contact.
        seq (int):
        text (str): The start of what was said; empty when withheld or deleted.
        deleted (bool | Unset): Its writer deleted it. Default: False.
        withheld (bool | Unset):  Default: False.
    """

    created_at: datetime.datetime
    id: UUID
    sender: PersonRef
    seq: int
    text: str
    deleted: bool | Unset = False
    withheld: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        id = str(self.id)

        sender = self.sender.to_dict()

        seq = self.seq

        text = self.text

        deleted = self.deleted

        withheld = self.withheld

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "sender": sender,
                "seq": seq,
                "text": text,
            }
        )
        if deleted is not UNSET:
            field_dict["deleted"] = deleted
        if withheld is not UNSET:
            field_dict["withheld"] = withheld

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.person_ref import PersonRef

        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        sender = PersonRef.from_dict(d.pop("sender"))

        seq = d.pop("seq")

        text = d.pop("text")

        deleted = d.pop("deleted", UNSET)

        withheld = d.pop("withheld", UNSET)

        line_preview = cls(
            created_at=created_at,
            id=id,
            sender=sender,
            seq=seq,
            text=text,
            deleted=deleted,
            withheld=withheld,
        )

        line_preview.additional_properties = d
        return line_preview

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
