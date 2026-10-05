from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.widget_answer import WidgetAnswer
from ..models.widget_kind import WidgetKind
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.form_spec import FormSpec


T = TypeVar("T", bound="WebWidgetCreatedResponse")


@_attrs_define
class WebWidgetCreatedResponse:
    """
    Attributes:
        agent_id (UUID):
        allowed_origins (list[str]):
        answer (WidgetAnswer): Whom a widget answers. Mirrors a bot's ``contacts.answer``.
        created_at (datetime.datetime):
        embed (str): The script tag that puts the widget on a page.
        form_function (None | str):
        form_requires_code (bool):
        id (UUID):
        kind (WidgetKind):
        looked_after_by (None | UUID):
        name (str):
        page_url (str): A page Lemma hosts with the form or chat on it, to share as a link.
        public_key (str):
        signing_secret (str): Signs host tokens on the customer's server. Shown this once; keep it off web pages.
        form (FormSpec | None | Unset):
    """

    agent_id: UUID
    allowed_origins: list[str]
    answer: WidgetAnswer
    created_at: datetime.datetime
    embed: str
    form_function: None | str
    form_requires_code: bool
    id: UUID
    kind: WidgetKind
    looked_after_by: None | UUID
    name: str
    page_url: str
    public_key: str
    signing_secret: str
    form: FormSpec | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.form_spec import FormSpec

        agent_id = str(self.agent_id)

        allowed_origins = self.allowed_origins

        answer = self.answer.value

        created_at = self.created_at.isoformat()

        embed = self.embed

        form_function: None | str
        form_function = self.form_function

        form_requires_code = self.form_requires_code

        id = str(self.id)

        kind = self.kind.value

        looked_after_by: None | str
        if isinstance(self.looked_after_by, UUID):
            looked_after_by = str(self.looked_after_by)
        else:
            looked_after_by = self.looked_after_by

        name = self.name

        page_url = self.page_url

        public_key = self.public_key

        signing_secret = self.signing_secret

        form: dict[str, Any] | None | Unset
        if isinstance(self.form, Unset):
            form = UNSET
        elif isinstance(self.form, FormSpec):
            form = self.form.to_dict()
        else:
            form = self.form

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent_id": agent_id,
                "allowed_origins": allowed_origins,
                "answer": answer,
                "created_at": created_at,
                "embed": embed,
                "form_function": form_function,
                "form_requires_code": form_requires_code,
                "id": id,
                "kind": kind,
                "looked_after_by": looked_after_by,
                "name": name,
                "page_url": page_url,
                "public_key": public_key,
                "signing_secret": signing_secret,
            }
        )
        if form is not UNSET:
            field_dict["form"] = form

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.form_spec import FormSpec

        d = dict(src_dict)
        agent_id = UUID(d.pop("agent_id"))

        allowed_origins = cast(list[str], d.pop("allowed_origins"))

        answer = WidgetAnswer(d.pop("answer"))

        created_at = isoparse(d.pop("created_at"))

        embed = d.pop("embed")

        def _parse_form_function(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        form_function = _parse_form_function(d.pop("form_function"))

        form_requires_code = d.pop("form_requires_code")

        id = UUID(d.pop("id"))

        kind = WidgetKind(d.pop("kind"))

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

        signing_secret = d.pop("signing_secret")

        def _parse_form(data: object) -> FormSpec | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                form_type_0 = FormSpec.from_dict(data)

                return form_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(FormSpec | None | Unset, data)

        form = _parse_form(d.pop("form", UNSET))

        web_widget_created_response = cls(
            agent_id=agent_id,
            allowed_origins=allowed_origins,
            answer=answer,
            created_at=created_at,
            embed=embed,
            form_function=form_function,
            form_requires_code=form_requires_code,
            id=id,
            kind=kind,
            looked_after_by=looked_after_by,
            name=name,
            page_url=page_url,
            public_key=public_key,
            signing_secret=signing_secret,
            form=form,
        )

        web_widget_created_response.additional_properties = d
        return web_widget_created_response

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
