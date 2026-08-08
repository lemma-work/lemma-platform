from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.notification_delivery_status import NotificationDeliveryStatus
from ..models.notification_origin_kind import NotificationOriginKind
from ..models.notification_status import NotificationStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.notification_response_action_type_0 import (
        NotificationResponseActionType0,
    )
    from ..models.notification_response_response_data_type_0 import (
        NotificationResponseResponseDataType0,
    )


T = TypeVar("T", bound="NotificationResponse")


@_attrs_define
class NotificationResponse:
    """One notification, shaped for the inbox that renders it.

    Carries enough to draw the row *and* decide what its action button does,
    without a second request: ``awaiting_response`` says whether to draw one at
    all, and ``responds_through_action`` says whether it opens a text box or the
    real form described by ``action``.

        Attributes:
            awaiting_response (bool):
            body (str):
            created_at (datetime.datetime):
            delivery_status (NotificationDeliveryStatus): Where the *channel* is: did the message physically get to them?

                Deliberately a second column rather than more members on
                :class:`NotificationStatus`. The two axes are independent — a notification
                can be DELIVERED and still OPEN (they haven't answered), or UNDELIVERABLE
                and still RESPONDED (they saw it in the app and replied there). Smearing
                them into one enum is how you end up unable to answer "who did we fail to
                reach?", which is the only question this column exists for.
            expects_response (bool):
            id (UUID):
            origin_kind (NotificationOriginKind): What produced the notification. ``origin_id`` is read against this.
            pod_id (UUID):
            responds_through_action (bool):
            status (NotificationStatus): Where the *person* is: has the thing we needed from them happened?
            title (str):
            action (None | NotificationResponseActionType0 | Unset):
            actor_agent_id (None | Unset | UUID):
            actor_user_id (None | Unset | UUID):
            delivered_at (datetime.datetime | None | Unset):
            delivery_conversation_id (None | Unset | UUID):
            delivery_platform (None | str | Unset):
            expires_at (datetime.datetime | None | Unset):
            origin_conversation_id (None | Unset | UUID):
            origin_id (None | Unset | UUID):
            read_at (datetime.datetime | None | Unset):
            responded_at (datetime.datetime | None | Unset):
            response_data (None | NotificationResponseResponseDataType0 | Unset):
            response_summary (None | str | Unset):
            undeliverable_reason (None | str | Unset):
    """

    awaiting_response: bool
    body: str
    created_at: datetime.datetime
    delivery_status: NotificationDeliveryStatus
    expects_response: bool
    id: UUID
    origin_kind: NotificationOriginKind
    pod_id: UUID
    responds_through_action: bool
    status: NotificationStatus
    title: str
    action: None | NotificationResponseActionType0 | Unset = UNSET
    actor_agent_id: None | Unset | UUID = UNSET
    actor_user_id: None | Unset | UUID = UNSET
    delivered_at: datetime.datetime | None | Unset = UNSET
    delivery_conversation_id: None | Unset | UUID = UNSET
    delivery_platform: None | str | Unset = UNSET
    expires_at: datetime.datetime | None | Unset = UNSET
    origin_conversation_id: None | Unset | UUID = UNSET
    origin_id: None | Unset | UUID = UNSET
    read_at: datetime.datetime | None | Unset = UNSET
    responded_at: datetime.datetime | None | Unset = UNSET
    response_data: None | NotificationResponseResponseDataType0 | Unset = UNSET
    response_summary: None | str | Unset = UNSET
    undeliverable_reason: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.notification_response_action_type_0 import (
            NotificationResponseActionType0,
        )
        from ..models.notification_response_response_data_type_0 import (
            NotificationResponseResponseDataType0,
        )

        awaiting_response = self.awaiting_response

        body = self.body

        created_at = self.created_at.isoformat()

        delivery_status = self.delivery_status.value

        expects_response = self.expects_response

        id = str(self.id)

        origin_kind = self.origin_kind.value

        pod_id = str(self.pod_id)

        responds_through_action = self.responds_through_action

        status = self.status.value

        title = self.title

        action: dict[str, Any] | None | Unset
        if isinstance(self.action, Unset):
            action = UNSET
        elif isinstance(self.action, NotificationResponseActionType0):
            action = self.action.to_dict()
        else:
            action = self.action

        actor_agent_id: None | str | Unset
        if isinstance(self.actor_agent_id, Unset):
            actor_agent_id = UNSET
        elif isinstance(self.actor_agent_id, UUID):
            actor_agent_id = str(self.actor_agent_id)
        else:
            actor_agent_id = self.actor_agent_id

        actor_user_id: None | str | Unset
        if isinstance(self.actor_user_id, Unset):
            actor_user_id = UNSET
        elif isinstance(self.actor_user_id, UUID):
            actor_user_id = str(self.actor_user_id)
        else:
            actor_user_id = self.actor_user_id

        delivered_at: None | str | Unset
        if isinstance(self.delivered_at, Unset):
            delivered_at = UNSET
        elif isinstance(self.delivered_at, datetime.datetime):
            delivered_at = self.delivered_at.isoformat()
        else:
            delivered_at = self.delivered_at

        delivery_conversation_id: None | str | Unset
        if isinstance(self.delivery_conversation_id, Unset):
            delivery_conversation_id = UNSET
        elif isinstance(self.delivery_conversation_id, UUID):
            delivery_conversation_id = str(self.delivery_conversation_id)
        else:
            delivery_conversation_id = self.delivery_conversation_id

        delivery_platform: None | str | Unset
        if isinstance(self.delivery_platform, Unset):
            delivery_platform = UNSET
        else:
            delivery_platform = self.delivery_platform

        expires_at: None | str | Unset
        if isinstance(self.expires_at, Unset):
            expires_at = UNSET
        elif isinstance(self.expires_at, datetime.datetime):
            expires_at = self.expires_at.isoformat()
        else:
            expires_at = self.expires_at

        origin_conversation_id: None | str | Unset
        if isinstance(self.origin_conversation_id, Unset):
            origin_conversation_id = UNSET
        elif isinstance(self.origin_conversation_id, UUID):
            origin_conversation_id = str(self.origin_conversation_id)
        else:
            origin_conversation_id = self.origin_conversation_id

        origin_id: None | str | Unset
        if isinstance(self.origin_id, Unset):
            origin_id = UNSET
        elif isinstance(self.origin_id, UUID):
            origin_id = str(self.origin_id)
        else:
            origin_id = self.origin_id

        read_at: None | str | Unset
        if isinstance(self.read_at, Unset):
            read_at = UNSET
        elif isinstance(self.read_at, datetime.datetime):
            read_at = self.read_at.isoformat()
        else:
            read_at = self.read_at

        responded_at: None | str | Unset
        if isinstance(self.responded_at, Unset):
            responded_at = UNSET
        elif isinstance(self.responded_at, datetime.datetime):
            responded_at = self.responded_at.isoformat()
        else:
            responded_at = self.responded_at

        response_data: dict[str, Any] | None | Unset
        if isinstance(self.response_data, Unset):
            response_data = UNSET
        elif isinstance(self.response_data, NotificationResponseResponseDataType0):
            response_data = self.response_data.to_dict()
        else:
            response_data = self.response_data

        response_summary: None | str | Unset
        if isinstance(self.response_summary, Unset):
            response_summary = UNSET
        else:
            response_summary = self.response_summary

        undeliverable_reason: None | str | Unset
        if isinstance(self.undeliverable_reason, Unset):
            undeliverable_reason = UNSET
        else:
            undeliverable_reason = self.undeliverable_reason

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "awaiting_response": awaiting_response,
                "body": body,
                "created_at": created_at,
                "delivery_status": delivery_status,
                "expects_response": expects_response,
                "id": id,
                "origin_kind": origin_kind,
                "pod_id": pod_id,
                "responds_through_action": responds_through_action,
                "status": status,
                "title": title,
            }
        )
        if action is not UNSET:
            field_dict["action"] = action
        if actor_agent_id is not UNSET:
            field_dict["actor_agent_id"] = actor_agent_id
        if actor_user_id is not UNSET:
            field_dict["actor_user_id"] = actor_user_id
        if delivered_at is not UNSET:
            field_dict["delivered_at"] = delivered_at
        if delivery_conversation_id is not UNSET:
            field_dict["delivery_conversation_id"] = delivery_conversation_id
        if delivery_platform is not UNSET:
            field_dict["delivery_platform"] = delivery_platform
        if expires_at is not UNSET:
            field_dict["expires_at"] = expires_at
        if origin_conversation_id is not UNSET:
            field_dict["origin_conversation_id"] = origin_conversation_id
        if origin_id is not UNSET:
            field_dict["origin_id"] = origin_id
        if read_at is not UNSET:
            field_dict["read_at"] = read_at
        if responded_at is not UNSET:
            field_dict["responded_at"] = responded_at
        if response_data is not UNSET:
            field_dict["response_data"] = response_data
        if response_summary is not UNSET:
            field_dict["response_summary"] = response_summary
        if undeliverable_reason is not UNSET:
            field_dict["undeliverable_reason"] = undeliverable_reason

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.notification_response_action_type_0 import (
            NotificationResponseActionType0,
        )
        from ..models.notification_response_response_data_type_0 import (
            NotificationResponseResponseDataType0,
        )

        d = dict(src_dict)
        awaiting_response = d.pop("awaiting_response")

        body = d.pop("body")

        created_at = isoparse(d.pop("created_at"))

        delivery_status = NotificationDeliveryStatus(d.pop("delivery_status"))

        expects_response = d.pop("expects_response")

        id = UUID(d.pop("id"))

        origin_kind = NotificationOriginKind(d.pop("origin_kind"))

        pod_id = UUID(d.pop("pod_id"))

        responds_through_action = d.pop("responds_through_action")

        status = NotificationStatus(d.pop("status"))

        title = d.pop("title")

        def _parse_action(
            data: object,
        ) -> None | NotificationResponseActionType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                action_type_0 = NotificationResponseActionType0.from_dict(data)

                return action_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | NotificationResponseActionType0 | Unset, data)

        action = _parse_action(d.pop("action", UNSET))

        def _parse_actor_agent_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                actor_agent_id_type_0 = UUID(data)

                return actor_agent_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        actor_agent_id = _parse_actor_agent_id(d.pop("actor_agent_id", UNSET))

        def _parse_actor_user_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                actor_user_id_type_0 = UUID(data)

                return actor_user_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        actor_user_id = _parse_actor_user_id(d.pop("actor_user_id", UNSET))

        def _parse_delivered_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                delivered_at_type_0 = isoparse(data)

                return delivered_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        delivered_at = _parse_delivered_at(d.pop("delivered_at", UNSET))

        def _parse_delivery_conversation_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                delivery_conversation_id_type_0 = UUID(data)

                return delivery_conversation_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        delivery_conversation_id = _parse_delivery_conversation_id(
            d.pop("delivery_conversation_id", UNSET)
        )

        def _parse_delivery_platform(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        delivery_platform = _parse_delivery_platform(d.pop("delivery_platform", UNSET))

        def _parse_expires_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                expires_at_type_0 = isoparse(data)

                return expires_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        expires_at = _parse_expires_at(d.pop("expires_at", UNSET))

        def _parse_origin_conversation_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                origin_conversation_id_type_0 = UUID(data)

                return origin_conversation_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        origin_conversation_id = _parse_origin_conversation_id(
            d.pop("origin_conversation_id", UNSET)
        )

        def _parse_origin_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                origin_id_type_0 = UUID(data)

                return origin_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        origin_id = _parse_origin_id(d.pop("origin_id", UNSET))

        def _parse_read_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                read_at_type_0 = isoparse(data)

                return read_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        read_at = _parse_read_at(d.pop("read_at", UNSET))

        def _parse_responded_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                responded_at_type_0 = isoparse(data)

                return responded_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        responded_at = _parse_responded_at(d.pop("responded_at", UNSET))

        def _parse_response_data(
            data: object,
        ) -> None | NotificationResponseResponseDataType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                response_data_type_0 = NotificationResponseResponseDataType0.from_dict(
                    data
                )

                return response_data_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | NotificationResponseResponseDataType0 | Unset, data)

        response_data = _parse_response_data(d.pop("response_data", UNSET))

        def _parse_response_summary(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        response_summary = _parse_response_summary(d.pop("response_summary", UNSET))

        def _parse_undeliverable_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        undeliverable_reason = _parse_undeliverable_reason(
            d.pop("undeliverable_reason", UNSET)
        )

        notification_response = cls(
            awaiting_response=awaiting_response,
            body=body,
            created_at=created_at,
            delivery_status=delivery_status,
            expects_response=expects_response,
            id=id,
            origin_kind=origin_kind,
            pod_id=pod_id,
            responds_through_action=responds_through_action,
            status=status,
            title=title,
            action=action,
            actor_agent_id=actor_agent_id,
            actor_user_id=actor_user_id,
            delivered_at=delivered_at,
            delivery_conversation_id=delivery_conversation_id,
            delivery_platform=delivery_platform,
            expires_at=expires_at,
            origin_conversation_id=origin_conversation_id,
            origin_id=origin_id,
            read_at=read_at,
            responded_at=responded_at,
            response_data=response_data,
            response_summary=response_summary,
            undeliverable_reason=undeliverable_reason,
        )

        notification_response.additional_properties = d
        return notification_response

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
