from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.automation_response import AutomationResponse
    from ..models.day_count_response import DayCountResponse
    from ..models.recent_chat_response import RecentChatResponse


T = TypeVar("T", bound="PodRecentsResponse")


@_attrs_define
class PodRecentsResponse:
    """
    Attributes:
        activity (list[DayCountResponse]): One entry per day of the window, oldest first, zeros included.
        automations (list[AutomationResponse]): Workflows and schedules that ran in the window, one each, the most
            recent first.
        chats (list[RecentChatResponse]): The member's latest chats, newest first.
        pod_id (UUID):
        pod_name (str):
    """

    activity: list[DayCountResponse]
    automations: list[AutomationResponse]
    chats: list[RecentChatResponse]
    pod_id: UUID
    pod_name: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        activity = []
        for activity_item_data in self.activity:
            activity_item = activity_item_data.to_dict()
            activity.append(activity_item)

        automations = []
        for automations_item_data in self.automations:
            automations_item = automations_item_data.to_dict()
            automations.append(automations_item)

        chats = []
        for chats_item_data in self.chats:
            chats_item = chats_item_data.to_dict()
            chats.append(chats_item)

        pod_id = str(self.pod_id)

        pod_name = self.pod_name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "activity": activity,
                "automations": automations,
                "chats": chats,
                "pod_id": pod_id,
                "pod_name": pod_name,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.automation_response import AutomationResponse
        from ..models.day_count_response import DayCountResponse
        from ..models.recent_chat_response import RecentChatResponse

        d = dict(src_dict)
        activity = []
        _activity = d.pop("activity")
        for activity_item_data in _activity:
            activity_item = DayCountResponse.from_dict(activity_item_data)

            activity.append(activity_item)

        automations = []
        _automations = d.pop("automations")
        for automations_item_data in _automations:
            automations_item = AutomationResponse.from_dict(automations_item_data)

            automations.append(automations_item)

        chats = []
        _chats = d.pop("chats")
        for chats_item_data in _chats:
            chats_item = RecentChatResponse.from_dict(chats_item_data)

            chats.append(chats_item)

        pod_id = UUID(d.pop("pod_id"))

        pod_name = d.pop("pod_name")

        pod_recents_response = cls(
            activity=activity,
            automations=automations,
            chats=chats,
            pod_id=pod_id,
            pod_name=pod_name,
        )

        pod_recents_response.additional_properties = d
        return pod_recents_response

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
