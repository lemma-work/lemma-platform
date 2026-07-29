from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.harness_runtime_config_config_selections import (
        HarnessRuntimeConfigConfigSelections,
    )


T = TypeVar("T", bound="HarnessRuntimeConfig")


@_attrs_define
class HarnessRuntimeConfig:
    """
    Attributes:
        harness_snapshot_revision (str):
        config_selections (HarnessRuntimeConfigConfigSelections | Unset):
        fallback_profile_id (None | str | Unset):
        host_wait_timeout_seconds (int | Unset):  Default: 300.
    """

    harness_snapshot_revision: str
    config_selections: HarnessRuntimeConfigConfigSelections | Unset = UNSET
    fallback_profile_id: None | str | Unset = UNSET
    host_wait_timeout_seconds: int | Unset = 300

    def to_dict(self) -> dict[str, Any]:
        harness_snapshot_revision = self.harness_snapshot_revision

        config_selections: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config_selections, Unset):
            config_selections = self.config_selections.to_dict()

        fallback_profile_id: None | str | Unset
        if isinstance(self.fallback_profile_id, Unset):
            fallback_profile_id = UNSET
        else:
            fallback_profile_id = self.fallback_profile_id

        host_wait_timeout_seconds = self.host_wait_timeout_seconds

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "harness_snapshot_revision": harness_snapshot_revision,
            }
        )
        if config_selections is not UNSET:
            field_dict["config_selections"] = config_selections
        if fallback_profile_id is not UNSET:
            field_dict["fallback_profile_id"] = fallback_profile_id
        if host_wait_timeout_seconds is not UNSET:
            field_dict["host_wait_timeout_seconds"] = host_wait_timeout_seconds

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.harness_runtime_config_config_selections import (
            HarnessRuntimeConfigConfigSelections,
        )

        d = dict(src_dict)
        harness_snapshot_revision = d.pop("harness_snapshot_revision")

        _config_selections = d.pop("config_selections", UNSET)
        config_selections: HarnessRuntimeConfigConfigSelections | Unset
        if isinstance(_config_selections, Unset):
            config_selections = UNSET
        else:
            config_selections = HarnessRuntimeConfigConfigSelections.from_dict(
                _config_selections
            )

        def _parse_fallback_profile_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        fallback_profile_id = _parse_fallback_profile_id(
            d.pop("fallback_profile_id", UNSET)
        )

        host_wait_timeout_seconds = d.pop("host_wait_timeout_seconds", UNSET)

        harness_runtime_config = cls(
            harness_snapshot_revision=harness_snapshot_revision,
            config_selections=config_selections,
            fallback_profile_id=fallback_profile_id,
            host_wait_timeout_seconds=host_wait_timeout_seconds,
        )

        return harness_runtime_config
