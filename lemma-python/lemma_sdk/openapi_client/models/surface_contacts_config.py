from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define

from ..models.contact_answer import ContactAnswer
from ..types import UNSET, Unset

T = TypeVar("T", bound="SurfaceContactsConfig")


@_attrs_define
class SurfaceContactsConfig:
    """Whom the bot answers in private chats beyond the pod's members. Mirrored.

    Attributes:
        answer (ContactAnswer | Unset): Whom a bot answers in a private chat, beyond the pod's members.
        looked_after_by (None | Unset | UUID): The member contacts' conversations belong to. Defaults to whoever turns
            contacts on; must be a member of the pod.
    """

    answer: ContactAnswer | Unset = UNSET
    looked_after_by: None | Unset | UUID = UNSET

    def to_dict(self) -> dict[str, Any]:
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

        field_dict.update({})
        if answer is not UNSET:
            field_dict["answer"] = answer
        if looked_after_by is not UNSET:
            field_dict["looked_after_by"] = looked_after_by

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        _answer = d.pop("answer", UNSET)
        answer: ContactAnswer | Unset
        if isinstance(_answer, Unset):
            answer = UNSET
        else:
            answer = ContactAnswer(_answer)

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

        surface_contacts_config = cls(
            answer=answer,
            looked_after_by=looked_after_by,
        )

        return surface_contacts_config
