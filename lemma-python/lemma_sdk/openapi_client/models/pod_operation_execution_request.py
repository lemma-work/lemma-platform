from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.pod_operation_execution_request_act_as import (
    PodOperationExecutionRequestActAs,
)
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.pod_operation_execution_request_payload import (
        PodOperationExecutionRequestPayload,
    )


T = TypeVar("T", bound="PodOperationExecutionRequest")


@_attrs_define
class PodOperationExecutionRequest:
    """A pod connector call's body: its arguments, and who it presents as.

    Attributes:
        payload (PodOperationExecutionRequestPayload): The operation's arguments. A file argument takes a reference --
            `{"pod_path": "/me/report.pdf"}`, `{"file_id": "..."}`, `{"url": "https://..."}` or `{"base64": "...",
            "filename": "..."}` -- read with the caller's own access before the call is made. `output_path` chooses where a
            file result lands in the pod.
        act_as (PodOperationExecutionRequestActAs | Unset): Who the call presents as. `user` (the default) is the
            account's own person. `app` is the connector's app -- for GitHub, the Lemma App's bot -- and only a function run
            may ask for it: anyone else is refused with `APP_IDENTITY_FUNCTIONS_ONLY`. It is the app or nothing: an
            operation only a person may call, an account with no App installation, or a connector with no app is refused
            with `APP_IDENTITY_UNAVAILABLE` before anything is sent. Default: PodOperationExecutionRequestActAs.USER.
    """

    payload: PodOperationExecutionRequestPayload
    act_as: PodOperationExecutionRequestActAs | Unset = (
        PodOperationExecutionRequestActAs.USER
    )
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = self.payload.to_dict()

        act_as: str | Unset = UNSET
        if not isinstance(self.act_as, Unset):
            act_as = self.act_as.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "payload": payload,
            }
        )
        if act_as is not UNSET:
            field_dict["act_as"] = act_as

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.pod_operation_execution_request_payload import (
            PodOperationExecutionRequestPayload,
        )

        d = dict(src_dict)
        payload = PodOperationExecutionRequestPayload.from_dict(d.pop("payload"))

        _act_as = d.pop("act_as", UNSET)
        act_as: PodOperationExecutionRequestActAs | Unset
        if isinstance(_act_as, Unset):
            act_as = UNSET
        else:
            act_as = PodOperationExecutionRequestActAs(_act_as)

        pod_operation_execution_request = cls(
            payload=payload,
            act_as=act_as,
        )

        pod_operation_execution_request.additional_properties = d
        return pod_operation_execution_request

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
