from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.pod_outcome import PodOutcome


T = TypeVar("T", bound="StartedPodResponse")


@_attrs_define
class StartedPodResponse:
    """
    Attributes:
        id (UUID):
        name (str): The name it got, numbered when the one asked for was taken.
        organization_id (UUID):
        created_at (datetime.datetime | None | Unset):
        description (None | str | Unset):
        icon_url (None | str | Unset):
        outcome (None | PodOutcome | Unset):
    """

    id: UUID
    name: str
    organization_id: UUID
    created_at: datetime.datetime | None | Unset = UNSET
    description: None | str | Unset = UNSET
    icon_url: None | str | Unset = UNSET
    outcome: None | PodOutcome | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.pod_outcome import PodOutcome

        id = str(self.id)

        name = self.name

        organization_id = str(self.organization_id)

        created_at: None | str | Unset
        if isinstance(self.created_at, Unset):
            created_at = UNSET
        elif isinstance(self.created_at, datetime.datetime):
            created_at = self.created_at.isoformat()
        else:
            created_at = self.created_at

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        icon_url: None | str | Unset
        if isinstance(self.icon_url, Unset):
            icon_url = UNSET
        else:
            icon_url = self.icon_url

        outcome: dict[str, Any] | None | Unset
        if isinstance(self.outcome, Unset):
            outcome = UNSET
        elif isinstance(self.outcome, PodOutcome):
            outcome = self.outcome.to_dict()
        else:
            outcome = self.outcome

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "name": name,
                "organization_id": organization_id,
            }
        )
        if created_at is not UNSET:
            field_dict["created_at"] = created_at
        if description is not UNSET:
            field_dict["description"] = description
        if icon_url is not UNSET:
            field_dict["icon_url"] = icon_url
        if outcome is not UNSET:
            field_dict["outcome"] = outcome

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.pod_outcome import PodOutcome

        d = dict(src_dict)
        id = UUID(d.pop("id"))

        name = d.pop("name")

        organization_id = UUID(d.pop("organization_id"))

        def _parse_created_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                created_at_type_0 = isoparse(data)

                return created_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        created_at = _parse_created_at(d.pop("created_at", UNSET))

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_icon_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        icon_url = _parse_icon_url(d.pop("icon_url", UNSET))

        def _parse_outcome(data: object) -> None | PodOutcome | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                outcome_type_0 = PodOutcome.from_dict(data)

                return outcome_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PodOutcome | Unset, data)

        outcome = _parse_outcome(d.pop("outcome", UNSET))

        started_pod_response = cls(
            id=id,
            name=name,
            organization_id=organization_id,
            created_at=created_at,
            description=description,
            icon_url=icon_url,
            outcome=outcome,
        )

        started_pod_response.additional_properties = d
        return started_pod_response

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
