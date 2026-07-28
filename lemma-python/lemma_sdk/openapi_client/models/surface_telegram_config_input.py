from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="SurfaceTelegramConfigInput")


@_attrs_define
class SurfaceTelegramConfigInput:
    """Selects the pod app exposed as this bot's Telegram Mini App.

    Attributes:
        app_id (None | Unset | UUID):
    """

    app_id: None | Unset | UUID = UNSET

    def to_dict(self) -> dict[str, Any]:
        app_id: None | str | Unset
        if isinstance(self.app_id, Unset):
            app_id = UNSET
        elif isinstance(self.app_id, UUID):
            app_id = str(self.app_id)
        else:
            app_id = self.app_id

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if app_id is not UNSET:
            field_dict["app_id"] = app_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_app_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                app_id_type_0 = UUID(data)

                return app_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        app_id = _parse_app_id(d.pop("app_id", UNSET))

        surface_telegram_config_input = cls(
            app_id=app_id,
        )

        return surface_telegram_config_input
