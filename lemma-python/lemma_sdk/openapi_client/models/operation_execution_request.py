from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.operation_execution_request_payload import (
        OperationExecutionRequestPayload,
    )


T = TypeVar("T", bound="OperationExecutionRequest")


@_attrs_define
class OperationExecutionRequest:
    """
    Attributes:
        payload (OperationExecutionRequestPayload): The operation's arguments. A file argument takes a reference --
            `{"pod_path": "/me/report.pdf"}`, `{"file_id": "..."}`, `{"url": "https://..."}` or `{"base64": "...",
            "filename": "..."}` -- read with the caller's own access before the call is made. `output_path` chooses where a
            file result lands in the pod.
    """

    payload: OperationExecutionRequestPayload
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = self.payload.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "payload": payload,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.operation_execution_request_payload import (
            OperationExecutionRequestPayload,
        )

        d = dict(src_dict)
        payload = OperationExecutionRequestPayload.from_dict(d.pop("payload"))

        operation_execution_request = cls(
            payload=payload,
        )

        operation_execution_request.additional_properties = d
        return operation_execution_request

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
