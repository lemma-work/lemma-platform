from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

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
        account_id (None | str | Unset):
        pod_id (None | Unset | UUID): The pod that `pod_path` and `file_id` references resolve in, and that file results
            land in. Implied for a call made from inside a pod; name it when calling as a person from outside one.
    """

    payload: OperationExecutionRequestPayload
    account_id: None | str | Unset = UNSET
    pod_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = self.payload.to_dict()

        account_id: None | str | Unset
        if isinstance(self.account_id, Unset):
            account_id = UNSET
        else:
            account_id = self.account_id

        pod_id: None | str | Unset
        if isinstance(self.pod_id, Unset):
            pod_id = UNSET
        elif isinstance(self.pod_id, UUID):
            pod_id = str(self.pod_id)
        else:
            pod_id = self.pod_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "payload": payload,
            }
        )
        if account_id is not UNSET:
            field_dict["account_id"] = account_id
        if pod_id is not UNSET:
            field_dict["pod_id"] = pod_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.operation_execution_request_payload import (
            OperationExecutionRequestPayload,
        )

        d = dict(src_dict)
        payload = OperationExecutionRequestPayload.from_dict(d.pop("payload"))

        def _parse_account_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        account_id = _parse_account_id(d.pop("account_id", UNSET))

        def _parse_pod_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                pod_id_type_0 = UUID(data)

                return pod_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        pod_id = _parse_pod_id(d.pop("pod_id", UNSET))

        operation_execution_request = cls(
            payload=payload,
            account_id=account_id,
            pod_id=pod_id,
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
