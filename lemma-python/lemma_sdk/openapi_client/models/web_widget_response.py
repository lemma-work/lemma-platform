from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.widget_answer import WidgetAnswer

T = TypeVar("T", bound="WebWidgetResponse")


@_attrs_define
class WebWidgetResponse:
    """
    Attributes:
        agent_id (UUID):
        allowed_origins (list[str]):
        answer (WidgetAnswer): Whom a widget answers. Mirrors a bot's ``contacts.answer``.
        created_at (datetime.datetime):
        embed (str): The script tag that puts the chat on a page.
        id (UUID):
        looked_after_by (None | UUID):
        name (str):
        page_url (str): A page Lemma hosts with the chat on it, to share as a link.
        public_key (str):
    """

    agent_id: UUID
    allowed_origins: list[str]
    answer: WidgetAnswer
    created_at: datetime.datetime
    embed: str
    id: UUID
    looked_after_by: None | UUID
    name: str
    page_url: str
    public_key: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent_id = str(self.agent_id)

        allowed_origins = self.allowed_origins

        answer = self.answer.value

        created_at = self.created_at.isoformat()

        embed = self.embed

        id = str(self.id)

        looked_after_by: None | str
        if isinstance(self.looked_after_by, UUID):
            looked_after_by = str(self.looked_after_by)
        else:
            looked_after_by = self.looked_after_by

        name = self.name

        page_url = self.page_url

        public_key = self.public_key

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent_id": agent_id,
                "allowed_origins": allowed_origins,
                "answer": answer,
                "created_at": created_at,
                "embed": embed,
                "id": id,
                "looked_after_by": looked_after_by,
                "name": name,
                "page_url": page_url,
                "public_key": public_key,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        agent_id = UUID(d.pop("agent_id"))

        allowed_origins = cast(list[str], d.pop("allowed_origins"))

        answer = WidgetAnswer(d.pop("answer"))

        created_at = isoparse(d.pop("created_at"))

        embed = d.pop("embed")

        id = UUID(d.pop("id"))

        def _parse_looked_after_by(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                looked_after_by_type_0 = UUID(data)

                return looked_after_by_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | UUID, data)

        looked_after_by = _parse_looked_after_by(d.pop("looked_after_by"))

        name = d.pop("name")

        page_url = d.pop("page_url")

        public_key = d.pop("public_key")

        web_widget_response = cls(
            agent_id=agent_id,
            allowed_origins=allowed_origins,
            answer=answer,
            created_at=created_at,
            embed=embed,
            id=id,
            looked_after_by=looked_after_by,
            name=name,
            page_url=page_url,
            public_key=public_key,
        )

        web_widget_response.additional_properties = d
        return web_widget_response

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
