from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.automation_kind import AutomationKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="AutomationResponse")


@_attrs_define
class AutomationResponse:
    """
    Attributes:
        id (UUID): The workflow's or the schedule's id.
        kind (AutomationKind):
        latest_at (datetime.datetime):
        latest_run_id (UUID): The newest workflow run, or the newest schedule fire.
        latest_status (str):
        run_count (int): Runs of the workflow, or fires of the schedule, in the window.
        conversation_id (None | Unset | UUID): The conversation a schedule's agent ran in, for the newest fire.
        name (None | str | Unset):
        workflow_run_id (None | Unset | UUID): The workflow run to open for the newest: the workflow's own, or the one a
            schedule started.
    """

    id: UUID
    kind: AutomationKind
    latest_at: datetime.datetime
    latest_run_id: UUID
    latest_status: str
    run_count: int
    conversation_id: None | Unset | UUID = UNSET
    name: None | str | Unset = UNSET
    workflow_run_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        kind = self.kind.value

        latest_at = self.latest_at.isoformat()

        latest_run_id = str(self.latest_run_id)

        latest_status = self.latest_status

        run_count = self.run_count

        conversation_id: None | str | Unset
        if isinstance(self.conversation_id, Unset):
            conversation_id = UNSET
        elif isinstance(self.conversation_id, UUID):
            conversation_id = str(self.conversation_id)
        else:
            conversation_id = self.conversation_id

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        workflow_run_id: None | str | Unset
        if isinstance(self.workflow_run_id, Unset):
            workflow_run_id = UNSET
        elif isinstance(self.workflow_run_id, UUID):
            workflow_run_id = str(self.workflow_run_id)
        else:
            workflow_run_id = self.workflow_run_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "kind": kind,
                "latest_at": latest_at,
                "latest_run_id": latest_run_id,
                "latest_status": latest_status,
                "run_count": run_count,
            }
        )
        if conversation_id is not UNSET:
            field_dict["conversation_id"] = conversation_id
        if name is not UNSET:
            field_dict["name"] = name
        if workflow_run_id is not UNSET:
            field_dict["workflow_run_id"] = workflow_run_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = UUID(d.pop("id"))

        kind = AutomationKind(d.pop("kind"))

        latest_at = isoparse(d.pop("latest_at"))

        latest_run_id = UUID(d.pop("latest_run_id"))

        latest_status = d.pop("latest_status")

        run_count = d.pop("run_count")

        def _parse_conversation_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                conversation_id_type_0 = UUID(data)

                return conversation_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        conversation_id = _parse_conversation_id(d.pop("conversation_id", UNSET))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_workflow_run_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                workflow_run_id_type_0 = UUID(data)

                return workflow_run_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        workflow_run_id = _parse_workflow_run_id(d.pop("workflow_run_id", UNSET))

        automation_response = cls(
            id=id,
            kind=kind,
            latest_at=latest_at,
            latest_run_id=latest_run_id,
            latest_status=latest_status,
            run_count=run_count,
            conversation_id=conversation_id,
            name=name,
            workflow_run_id=workflow_run_id,
        )

        automation_response.additional_properties = d
        return automation_response

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
