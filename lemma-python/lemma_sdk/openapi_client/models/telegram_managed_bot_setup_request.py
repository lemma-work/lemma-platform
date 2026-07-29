from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.surface_behavior_config_input import SurfaceBehaviorConfigInput


T = TypeVar("T", bound="TelegramManagedBotSetupRequest")


@_attrs_define
class TelegramManagedBotSetupRequest:
    """
    Attributes:
        config (SurfaceBehaviorConfigInput | Unset):
        default_agent_name (None | str | Unset):
        is_enabled (bool | Unset):  Default: True.
        name (None | str | Unset): Pod-unique surface name. Defaults to telegram.
    """

    config: SurfaceBehaviorConfigInput | Unset = UNSET
    default_agent_name: None | str | Unset = UNSET
    is_enabled: bool | Unset = True
    name: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config, Unset):
            config = self.config.to_dict()

        default_agent_name: None | str | Unset
        if isinstance(self.default_agent_name, Unset):
            default_agent_name = UNSET
        else:
            default_agent_name = self.default_agent_name

        is_enabled = self.is_enabled

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if config is not UNSET:
            field_dict["config"] = config
        if default_agent_name is not UNSET:
            field_dict["default_agent_name"] = default_agent_name
        if is_enabled is not UNSET:
            field_dict["is_enabled"] = is_enabled
        if name is not UNSET:
            field_dict["name"] = name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.surface_behavior_config_input import SurfaceBehaviorConfigInput

        d = dict(src_dict)
        _config = d.pop("config", UNSET)
        config: SurfaceBehaviorConfigInput | Unset
        if isinstance(_config, Unset):
            config = UNSET
        else:
            config = SurfaceBehaviorConfigInput.from_dict(_config)

        def _parse_default_agent_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        default_agent_name = _parse_default_agent_name(
            d.pop("default_agent_name", UNSET)
        )

        is_enabled = d.pop("is_enabled", UNSET)

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        telegram_managed_bot_setup_request = cls(
            config=config,
            default_agent_name=default_agent_name,
            is_enabled=is_enabled,
            name=name,
        )

        return telegram_managed_bot_setup_request
