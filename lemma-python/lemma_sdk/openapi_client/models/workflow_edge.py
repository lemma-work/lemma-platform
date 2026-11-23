from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.workflow_edge_kind import WorkflowEdgeKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="WorkflowEdge")


@_attrs_define
class WorkflowEdge:
    """An edge in the workflow graph.

    Attributes:
        id (str):
        source (str):
        target (str):
        kind (WorkflowEdgeKind | Unset): What an edge is followed on.
        label (None | str | Unset):
    """

    id: str
    source: str
    target: str
    kind: WorkflowEdgeKind | Unset = UNSET
    label: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        source = self.source

        target = self.target

        kind: str | Unset = UNSET
        if not isinstance(self.kind, Unset):
            kind = self.kind.value

        label: None | str | Unset
        if isinstance(self.label, Unset):
            label = UNSET
        else:
            label = self.label

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "source": source,
                "target": target,
            }
        )
        if kind is not UNSET:
            field_dict["kind"] = kind
        if label is not UNSET:
            field_dict["label"] = label

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        source = d.pop("source")

        target = d.pop("target")

        _kind = d.pop("kind", UNSET)
        kind: WorkflowEdgeKind | Unset
        if isinstance(_kind, Unset):
            kind = UNSET
        else:
            kind = WorkflowEdgeKind(_kind)

        def _parse_label(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        label = _parse_label(d.pop("label", UNSET))

        workflow_edge = cls(
            id=id,
            source=source,
            target=target,
            kind=kind,
            label=label,
        )

        workflow_edge.additional_properties = d
        return workflow_edge

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
