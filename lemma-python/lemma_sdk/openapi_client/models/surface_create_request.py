from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define

from ..models.surface_credential_mode import SurfaceCredentialMode
from ..models.surface_platform import SurfacePlatform
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.surface_behavior_config_input import SurfaceBehaviorConfigInput


T = TypeVar("T", bound="SurfaceCreateRequest")


@_attrs_define
class SurfaceCreateRequest:
    """Body for `POST /pods/{pod_id}/surfaces` — creates one surface.

    A pod may have several surfaces of the same ``platform`` (different
    bots/accounts, each routed to its own agent); ``name`` is the stable,
    pod-unique identifier used to address it afterward. When omitted, it
    defaults to the lowercased platform (so the common single-surface-per-
    platform case needs no name at all) — pick an explicit name to create a
    second surface of the same platform.

        Attributes:
            platform (SurfacePlatform): The platforms a pod can be reached on.

                Email is Resend, and only Resend. Gmail and Outlook were here as
                Composio-backed mailboxes, which made "an email surface" mean three
                different transports with three attachment strategies between them -- bytes,
                Graph drafts, and a signed URL the provider downloads server-side. Reaching
                a Gmail *account* is still something an agent does, through the connector;
                it is just not a surface.
            account_id (None | Unset | UUID):
            config (SurfaceBehaviorConfigInput | Unset):
            credential_mode (SurfaceCredentialMode | Unset):
            default_agent_name (None | str | Unset):
            is_enabled (bool | Unset):  Default: True.
            name (None | str | Unset): Pod-unique surface identifier. Defaults to the lowercased platform.
    """

    platform: SurfacePlatform
    account_id: None | Unset | UUID = UNSET
    config: SurfaceBehaviorConfigInput | Unset = UNSET
    credential_mode: SurfaceCredentialMode | Unset = UNSET
    default_agent_name: None | str | Unset = UNSET
    is_enabled: bool | Unset = True
    name: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        platform = self.platform.value

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

        credential_mode: str | Unset = UNSET
        if not isinstance(self.credential_mode, Unset):
            credential_mode = self.credential_mode.value

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

        field_dict.update(
            {
                "platform": platform,
            }
        )
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
        if name is not UNSET:
            field_dict["name"] = name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.surface_behavior_config_input import SurfaceBehaviorConfigInput

        d = dict(src_dict)
        platform = SurfacePlatform(d.pop("platform"))

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

        _credential_mode = d.pop("credential_mode", UNSET)
        credential_mode: SurfaceCredentialMode | Unset
        if isinstance(_credential_mode, Unset):
            credential_mode = UNSET
        else:
            credential_mode = SurfaceCredentialMode(_credential_mode)

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

        surface_create_request = cls(
            platform=platform,
            account_id=account_id,
            config=config,
            credential_mode=credential_mode,
            default_agent_name=default_agent_name,
            is_enabled=is_enabled,
            name=name,
        )

        return surface_create_request
