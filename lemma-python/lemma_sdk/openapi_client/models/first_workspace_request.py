from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="FirstWorkspaceRequest")


@_attrs_define
class FirstWorkspaceRequest:
    """
    Attributes:
        with_pod (bool | Unset): Ensure a personal pod and assistant. Importers may request only an organization.
            Default: True.
    """

    with_pod: bool | Unset = True

    def to_dict(self) -> dict[str, Any]:
        with_pod = self.with_pod

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if with_pod is not UNSET:
            field_dict["with_pod"] = with_pod

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        with_pod = d.pop("with_pod", UNSET)

        first_workspace_request = cls(
            with_pod=with_pod,
        )

        return first_workspace_request
