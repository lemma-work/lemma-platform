from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.pod_join_policy import PodJoinPolicy
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_runtime_config import AgentRuntimeConfig


T = TypeVar("T", bound="PodConfig")


@_attrs_define
class PodConfig:
    """Typed pod-level configuration.

    Attributes:
        default_profile_id (None | str | Unset):
        default_runtime (AgentRuntimeConfig | None | Unset):
        join_policy (PodJoinPolicy | Unset): Who may self-join a pod, ordered from closed to open.
    """

    default_profile_id: None | str | Unset = UNSET
    default_runtime: AgentRuntimeConfig | None | Unset = UNSET
    join_policy: PodJoinPolicy | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_runtime_config import AgentRuntimeConfig

        default_profile_id: None | str | Unset
        if isinstance(self.default_profile_id, Unset):
            default_profile_id = UNSET
        else:
            default_profile_id = self.default_profile_id

        default_runtime: dict[str, Any] | None | Unset
        if isinstance(self.default_runtime, Unset):
            default_runtime = UNSET
        elif isinstance(self.default_runtime, AgentRuntimeConfig):
            default_runtime = self.default_runtime.to_dict()
        else:
            default_runtime = self.default_runtime

        join_policy: str | Unset = UNSET
        if not isinstance(self.join_policy, Unset):
            join_policy = self.join_policy.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if default_profile_id is not UNSET:
            field_dict["default_profile_id"] = default_profile_id
        if default_runtime is not UNSET:
            field_dict["default_runtime"] = default_runtime
        if join_policy is not UNSET:
            field_dict["join_policy"] = join_policy

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_runtime_config import AgentRuntimeConfig

        d = dict(src_dict)

        def _parse_default_profile_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        default_profile_id = _parse_default_profile_id(
            d.pop("default_profile_id", UNSET)
        )

        def _parse_default_runtime(data: object) -> AgentRuntimeConfig | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                default_runtime_type_0 = AgentRuntimeConfig.from_dict(data)

                return default_runtime_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AgentRuntimeConfig | None | Unset, data)

        default_runtime = _parse_default_runtime(d.pop("default_runtime", UNSET))

        _join_policy = d.pop("join_policy", UNSET)
        join_policy: PodJoinPolicy | Unset
        if isinstance(_join_policy, Unset):
            join_policy = UNSET
        else:
            join_policy = PodJoinPolicy(_join_policy)

        pod_config = cls(
            default_profile_id=default_profile_id,
            default_runtime=default_runtime,
            join_policy=join_policy,
        )

        pod_config.additional_properties = d
        return pod_config

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
