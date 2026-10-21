from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.pod_join_policy import PodJoinPolicy
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_runtime_config import AgentRuntimeConfig
    from ..models.pod_contact_settings import PodContactSettings
    from ..models.pod_recipe import PodRecipe
    from ..models.pod_trigger_settings import PodTriggerSettings


T = TypeVar("T", bound="PodConfig")


@_attrs_define
class PodConfig:
    """Typed pod-level configuration.

    Attributes:
        contacts (None | PodContactSettings | Unset):
        default_profile_id (None | str | Unset):
        default_runtime (AgentRuntimeConfig | None | Unset):
        join_policy (PodJoinPolicy | Unset): Who may self-join a pod, ordered from closed to open.
        recipes (list[PodRecipe] | Unset):
        triggers (None | PodTriggerSettings | Unset):
    """

    contacts: None | PodContactSettings | Unset = UNSET
    default_profile_id: None | str | Unset = UNSET
    default_runtime: AgentRuntimeConfig | None | Unset = UNSET
    join_policy: PodJoinPolicy | Unset = UNSET
    recipes: list[PodRecipe] | Unset = UNSET
    triggers: None | PodTriggerSettings | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_runtime_config import AgentRuntimeConfig
        from ..models.pod_contact_settings import PodContactSettings
        from ..models.pod_trigger_settings import PodTriggerSettings

        contacts: dict[str, Any] | None | Unset
        if isinstance(self.contacts, Unset):
            contacts = UNSET
        elif isinstance(self.contacts, PodContactSettings):
            contacts = self.contacts.to_dict()
        else:
            contacts = self.contacts

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

        recipes: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.recipes, Unset):
            recipes = []
            for recipes_item_data in self.recipes:
                recipes_item = recipes_item_data.to_dict()
                recipes.append(recipes_item)

        triggers: dict[str, Any] | None | Unset
        if isinstance(self.triggers, Unset):
            triggers = UNSET
        elif isinstance(self.triggers, PodTriggerSettings):
            triggers = self.triggers.to_dict()
        else:
            triggers = self.triggers

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if contacts is not UNSET:
            field_dict["contacts"] = contacts
        if default_profile_id is not UNSET:
            field_dict["default_profile_id"] = default_profile_id
        if default_runtime is not UNSET:
            field_dict["default_runtime"] = default_runtime
        if join_policy is not UNSET:
            field_dict["join_policy"] = join_policy
        if recipes is not UNSET:
            field_dict["recipes"] = recipes
        if triggers is not UNSET:
            field_dict["triggers"] = triggers

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_runtime_config import AgentRuntimeConfig
        from ..models.pod_contact_settings import PodContactSettings
        from ..models.pod_recipe import PodRecipe
        from ..models.pod_trigger_settings import PodTriggerSettings

        d = dict(src_dict)

        def _parse_contacts(data: object) -> None | PodContactSettings | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                contacts_type_0 = PodContactSettings.from_dict(data)

                return contacts_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PodContactSettings | Unset, data)

        contacts = _parse_contacts(d.pop("contacts", UNSET))

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
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AgentRuntimeConfig | None | Unset, data)

        default_runtime = _parse_default_runtime(d.pop("default_runtime", UNSET))

        _join_policy = d.pop("join_policy", UNSET)
        join_policy: PodJoinPolicy | Unset
        if isinstance(_join_policy, Unset):
            join_policy = UNSET
        else:
            join_policy = PodJoinPolicy(_join_policy)

        _recipes = d.pop("recipes", UNSET)
        recipes: list[PodRecipe] | Unset = UNSET
        if _recipes is not UNSET:
            recipes = []
            for recipes_item_data in _recipes:
                recipes_item = PodRecipe.from_dict(recipes_item_data)

                recipes.append(recipes_item)

        def _parse_triggers(data: object) -> None | PodTriggerSettings | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                triggers_type_0 = PodTriggerSettings.from_dict(data)

                return triggers_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PodTriggerSettings | Unset, data)

        triggers = _parse_triggers(d.pop("triggers", UNSET))

        pod_config = cls(
            contacts=contacts,
            default_profile_id=default_profile_id,
            default_runtime=default_runtime,
            join_policy=join_policy,
            recipes=recipes,
            triggers=triggers,
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
