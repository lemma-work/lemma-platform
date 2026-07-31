from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define

from ..models.surface_credential_mode import SurfaceCredentialMode
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.surface_behavior_config_input import SurfaceBehaviorConfigInput


T = TypeVar("T", bound="SurfaceUpdateRequest")


@_attrs_define
class SurfaceUpdateRequest:
    """Body for `PATCH /pods/{pod_id}/surfaces/{surface_name}`.

    Partial update (merge semantics): only fields present in the request are
    applied. The surface's ``platform`` and ``name`` are immutable — delete and
    recreate to change either.

        Attributes:
            account_id (None | Unset | UUID):
            config (SurfaceBehaviorConfigInput | Unset):
            credential_mode (None | SurfaceCredentialMode | Unset):
            default_agent_name (None | str | Unset):
            is_enabled (bool | None | Unset):
    """

    account_id: None | Unset | UUID = UNSET
    config: SurfaceBehaviorConfigInput | Unset = UNSET
    credential_mode: None | SurfaceCredentialMode | Unset = UNSET
    default_agent_name: None | str | Unset = UNSET
    is_enabled: bool | None | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        account_id: None | str | Unset
        if isinstance(self.account_id, Unset):
            account_id = UNSET
        elif isinstance(self.account_id, UUID):
            account_id = str(self.account_id)
        else:
            account_id = self.account_id

        config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config, Unset):
            config = self.config.to_dict()

        credential_mode: None | str | Unset
        if isinstance(self.credential_mode, Unset):
            credential_mode = UNSET
        elif isinstance(self.credential_mode, SurfaceCredentialMode):
            credential_mode = self.credential_mode.value
        else:
            credential_mode = self.credential_mode

        default_agent_name: None | str | Unset
        if isinstance(self.default_agent_name, Unset):
            default_agent_name = UNSET
        else:
            default_agent_name = self.default_agent_name

        is_enabled: bool | None | Unset
        if isinstance(self.is_enabled, Unset):
            is_enabled = UNSET
        else:
            is_enabled = self.is_enabled

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if account_id is not UNSET:
            field_dict["account_id"] = account_id
        if config is not UNSET:
            field_dict["config"] = config
        if credential_mode is not UNSET:
            field_dict["credential_mode"] = credential_mode
        if default_agent_name is not UNSET:
            field_dict["default_agent_name"] = default_agent_name
        if is_enabled is not UNSET:
            field_dict["is_enabled"] = is_enabled

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.surface_behavior_config_input import SurfaceBehaviorConfigInput

        d = dict(src_dict)

        def _parse_account_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                account_id_type_0 = UUID(data)

                return account_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        account_id = _parse_account_id(d.pop("account_id", UNSET))

        _config = d.pop("config", UNSET)
        config: SurfaceBehaviorConfigInput | Unset
        if isinstance(_config, Unset):
            config = UNSET
        else:
            config = SurfaceBehaviorConfigInput.from_dict(_config)

        def _parse_credential_mode(
            data: object,
        ) -> None | SurfaceCredentialMode | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                credential_mode_type_0 = SurfaceCredentialMode(data)

                return credential_mode_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | SurfaceCredentialMode | Unset, data)

        credential_mode = _parse_credential_mode(d.pop("credential_mode", UNSET))

        def _parse_default_agent_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        default_agent_name = _parse_default_agent_name(
            d.pop("default_agent_name", UNSET)
        )

        def _parse_is_enabled(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        is_enabled = _parse_is_enabled(d.pop("is_enabled", UNSET))

        surface_update_request = cls(
            account_id=account_id,
            config=config,
            credential_mode=credential_mode,
            default_agent_name=default_agent_name,
            is_enabled=is_enabled,
        )

        return surface_update_request
