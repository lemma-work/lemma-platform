from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.delivery_status import DeliveryStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.delivery_target import DeliveryTarget


T = TypeVar("T", bound="DeliveryResponse")


@_attrs_define
class DeliveryResponse:
    """
    Attributes:
        id (UUID):
        message_id (UUID):
        status (DeliveryStatus):
        target (DeliveryTarget):
        attempts (int | Unset):  Default: 0.
        created_at (datetime.datetime | None | Unset):
        next_attempt_at (datetime.datetime | None | Unset):
        platform (None | str | Unset):
        reason (None | str | Unset):
        sent_at (datetime.datetime | None | Unset):
    """

    id: UUID
    message_id: UUID
    status: DeliveryStatus
    target: DeliveryTarget
    attempts: int | Unset = 0
    created_at: datetime.datetime | None | Unset = UNSET
    next_attempt_at: datetime.datetime | None | Unset = UNSET
    platform: None | str | Unset = UNSET
    reason: None | str | Unset = UNSET
    sent_at: datetime.datetime | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        message_id = str(self.message_id)

        status = self.status.value

        target = self.target.to_dict()

        attempts = self.attempts

        created_at: None | str | Unset
        if isinstance(self.created_at, Unset):
            created_at = UNSET
        elif isinstance(self.created_at, datetime.datetime):
            created_at = self.created_at.isoformat()
        else:
            created_at = self.created_at

        next_attempt_at: None | str | Unset
        if isinstance(self.next_attempt_at, Unset):
            next_attempt_at = UNSET
        elif isinstance(self.next_attempt_at, datetime.datetime):
            next_attempt_at = self.next_attempt_at.isoformat()
        else:
            next_attempt_at = self.next_attempt_at

        platform: None | str | Unset
        if isinstance(self.platform, Unset):
            platform = UNSET
        else:
            platform = self.platform

        reason: None | str | Unset
        if isinstance(self.reason, Unset):
            reason = UNSET
        else:
            reason = self.reason

        sent_at: None | str | Unset
        if isinstance(self.sent_at, Unset):
            sent_at = UNSET
        elif isinstance(self.sent_at, datetime.datetime):
            sent_at = self.sent_at.isoformat()
        else:
            sent_at = self.sent_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "message_id": message_id,
                "status": status,
                "target": target,
            }
        )
        if attempts is not UNSET:
            field_dict["attempts"] = attempts
        if created_at is not UNSET:
            field_dict["created_at"] = created_at
        if next_attempt_at is not UNSET:
            field_dict["next_attempt_at"] = next_attempt_at
        if platform is not UNSET:
            field_dict["platform"] = platform
        if reason is not UNSET:
            field_dict["reason"] = reason
        if sent_at is not UNSET:
            field_dict["sent_at"] = sent_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.delivery_target import DeliveryTarget

        d = dict(src_dict)
        id = UUID(d.pop("id"))

        message_id = UUID(d.pop("message_id"))

        status = DeliveryStatus(d.pop("status"))

        target = DeliveryTarget.from_dict(d.pop("target"))

        attempts = d.pop("attempts", UNSET)

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

        def _parse_next_attempt_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                next_attempt_at_type_0 = isoparse(data)

                return next_attempt_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        next_attempt_at = _parse_next_attempt_at(d.pop("next_attempt_at", UNSET))

        def _parse_platform(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        platform = _parse_platform(d.pop("platform", UNSET))

        def _parse_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        reason = _parse_reason(d.pop("reason", UNSET))

        def _parse_sent_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                sent_at_type_0 = isoparse(data)

                return sent_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        sent_at = _parse_sent_at(d.pop("sent_at", UNSET))

        delivery_response = cls(
            id=id,
            message_id=message_id,
            status=status,
            target=target,
            attempts=attempts,
            created_at=created_at,
            next_attempt_at=next_attempt_at,
            platform=platform,
            reason=reason,
            sent_at=sent_at,
        )

        delivery_response.additional_properties = d
        return delivery_response

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
