from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.resource_visibility import ResourceVisibility
from ..models.workflow_mode import WorkflowMode
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.data_store_workflow_start_input import DataStoreWorkflowStartInput
    from ..models.event_workflow_start_input import EventWorkflowStartInput
    from ..models.manual_workflow_start_input import ManualWorkflowStartInput
    from ..models.scheduled_workflow_start_input import ScheduledWorkflowStartInput


T = TypeVar("T", bound="WorkflowUpdateRequest")


@_attrs_define
class WorkflowUpdateRequest:
    """
    Attributes:
        description (None | str | Unset): Updated workflow description.
        icon_url (None | str | Unset): Updated public icon URL for the workflow.
        mode (None | Unset | WorkflowMode): Updated workflow schedule ownership mode.
        start (DataStoreWorkflowStartInput | EventWorkflowStartInput | ManualWorkflowStartInput | None |
            ScheduledWorkflowStartInput | Unset): Updated start trigger configuration.
        visibility (None | ResourceVisibility | Unset):
    """

    description: None | str | Unset = UNSET
    icon_url: None | str | Unset = UNSET
    mode: None | Unset | WorkflowMode = UNSET
    start: (
        DataStoreWorkflowStartInput
        | EventWorkflowStartInput
        | ManualWorkflowStartInput
        | None
        | ScheduledWorkflowStartInput
        | Unset
    ) = UNSET
    visibility: None | ResourceVisibility | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.data_store_workflow_start_input import DataStoreWorkflowStartInput
        from ..models.event_workflow_start_input import EventWorkflowStartInput
        from ..models.manual_workflow_start_input import ManualWorkflowStartInput
        from ..models.scheduled_workflow_start_input import ScheduledWorkflowStartInput

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

        mode: None | str | Unset
        if isinstance(self.mode, Unset):
            mode = UNSET
        elif isinstance(self.mode, WorkflowMode):
            mode = self.mode.value
        else:
            mode = self.mode

        start: dict[str, Any] | None | Unset
        if isinstance(self.start, Unset):
            start = UNSET
        elif isinstance(self.start, ManualWorkflowStartInput):
            start = self.start.to_dict()
        elif isinstance(self.start, ScheduledWorkflowStartInput):
            start = self.start.to_dict()
        elif isinstance(self.start, EventWorkflowStartInput):
            start = self.start.to_dict()
        elif isinstance(self.start, DataStoreWorkflowStartInput):
            start = self.start.to_dict()
        else:
            start = self.start

        visibility: None | str | Unset
        if isinstance(self.visibility, Unset):
            visibility = UNSET
        elif isinstance(self.visibility, ResourceVisibility):
            visibility = self.visibility.value
        else:
            visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if description is not UNSET:
            field_dict["description"] = description
        if icon_url is not UNSET:
            field_dict["icon_url"] = icon_url
        if mode is not UNSET:
            field_dict["mode"] = mode
        if start is not UNSET:
            field_dict["start"] = start
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.data_store_workflow_start_input import DataStoreWorkflowStartInput
        from ..models.event_workflow_start_input import EventWorkflowStartInput
        from ..models.manual_workflow_start_input import ManualWorkflowStartInput
        from ..models.scheduled_workflow_start_input import ScheduledWorkflowStartInput

        d = dict(src_dict)

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

        def _parse_mode(data: object) -> None | Unset | WorkflowMode:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                mode_type_0 = WorkflowMode(data)

                return mode_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | WorkflowMode, data)

        mode = _parse_mode(d.pop("mode", UNSET))

        def _parse_start(
            data: object,
        ) -> (
            DataStoreWorkflowStartInput
            | EventWorkflowStartInput
            | ManualWorkflowStartInput
            | None
            | ScheduledWorkflowStartInput
            | Unset
        ):
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                start_type_0_type_0 = ManualWorkflowStartInput.from_dict(data)

                return start_type_0_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                start_type_0_type_1 = ScheduledWorkflowStartInput.from_dict(data)

                return start_type_0_type_1
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                start_type_0_type_2 = EventWorkflowStartInput.from_dict(data)

                return start_type_0_type_2
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                start_type_0_type_3 = DataStoreWorkflowStartInput.from_dict(data)

                return start_type_0_type_3
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(
                DataStoreWorkflowStartInput
                | EventWorkflowStartInput
                | ManualWorkflowStartInput
                | None
                | ScheduledWorkflowStartInput
                | Unset,
                data,
            )

        start = _parse_start(d.pop("start", UNSET))

        def _parse_visibility(data: object) -> None | ResourceVisibility | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                visibility_type_0 = ResourceVisibility(data)

                return visibility_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | ResourceVisibility | Unset, data)

        visibility = _parse_visibility(d.pop("visibility", UNSET))

        workflow_update_request = cls(
            description=description,
            icon_url=icon_url,
            mode=mode,
            start=start,
            visibility=visibility,
        )

        workflow_update_request.additional_properties = d
        return workflow_update_request

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
