from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_runtime_config_config_selections_type_0 import (
        AgentRuntimeConfigConfigSelectionsType0,
    )
    from ..models.generation_settings import GenerationSettings


T = TypeVar("T", bound="AgentRuntimeConfig")


@_attrs_define
class AgentRuntimeConfig:
    """Select an agent runtime profile, an optional catalog model, and how it generates.

    Attributes:
        profile_id (str):
        config_selections (AgentRuntimeConfigConfigSelectionsType0 | None | Unset):
        model_name (None | str | Unset):
        settings (GenerationSettings | None | Unset):
    """

    profile_id: str
    config_selections: AgentRuntimeConfigConfigSelectionsType0 | None | Unset = UNSET
    model_name: None | str | Unset = UNSET
    settings: GenerationSettings | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_runtime_config_config_selections_type_0 import (
            AgentRuntimeConfigConfigSelectionsType0,
        )
        from ..models.generation_settings import GenerationSettings

        profile_id = self.profile_id

        config_selections: dict[str, Any] | None | Unset
        if isinstance(self.config_selections, Unset):
            config_selections = UNSET
        elif isinstance(
            self.config_selections, AgentRuntimeConfigConfigSelectionsType0
        ):
            config_selections = self.config_selections.to_dict()
        else:
            config_selections = self.config_selections

        model_name: None | str | Unset
        if isinstance(self.model_name, Unset):
            model_name = UNSET
        else:
            model_name = self.model_name

        settings: dict[str, Any] | None | Unset
        if isinstance(self.settings, Unset):
            settings = UNSET
        elif isinstance(self.settings, GenerationSettings):
            settings = self.settings.to_dict()
        else:
            settings = self.settings

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "profile_id": profile_id,
            }
        )
        if config_selections is not UNSET:
            field_dict["config_selections"] = config_selections
        if model_name is not UNSET:
            field_dict["model_name"] = model_name
        if settings is not UNSET:
            field_dict["settings"] = settings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_runtime_config_config_selections_type_0 import (
            AgentRuntimeConfigConfigSelectionsType0,
        )
        from ..models.generation_settings import GenerationSettings

        d = dict(src_dict)
        profile_id = d.pop("profile_id")

        def _parse_config_selections(
            data: object,
        ) -> AgentRuntimeConfigConfigSelectionsType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_selections_type_0 = (
                    AgentRuntimeConfigConfigSelectionsType0.from_dict(data)
                )

                return config_selections_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AgentRuntimeConfigConfigSelectionsType0 | None | Unset, data)

        config_selections = _parse_config_selections(d.pop("config_selections", UNSET))

        def _parse_model_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        model_name = _parse_model_name(d.pop("model_name", UNSET))

        def _parse_settings(data: object) -> GenerationSettings | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                settings_type_0 = GenerationSettings.from_dict(data)

                return settings_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(GenerationSettings | None | Unset, data)

        settings = _parse_settings(d.pop("settings", UNSET))

        agent_runtime_config = cls(
            profile_id=profile_id,
            config_selections=config_selections,
            model_name=model_name,
            settings=settings,
        )

        agent_runtime_config.additional_properties = d
        return agent_runtime_config

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
