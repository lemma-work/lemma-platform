from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="SurfaceTelegramConfigInput")


@_attrs_define
class SurfaceTelegramConfigInput:
    """Selects the pod app exposed as this bot's Telegram Mini App.

    Attributes:
        app_name (None | str | Unset):
    """

    app_name: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        app_name: None | str | Unset
        if isinstance(self.app_name, Unset):
            app_name = UNSET
        else:
            app_name = self.app_name

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if app_name is not UNSET:
            field_dict["app_name"] = app_name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_app_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        app_name = _parse_app_name(d.pop("app_name", UNSET))

        surface_telegram_config_input = cls(
            app_name=app_name,
        )

        return surface_telegram_config_input
