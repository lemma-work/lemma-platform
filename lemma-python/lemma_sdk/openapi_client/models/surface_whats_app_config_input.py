from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="SurfaceWhatsAppConfigInput")


@_attrs_define
class SurfaceWhatsAppConfigInput:
    """WhatsApp settings. Mirrored across request and response.

    Attributes:
        language (str | Unset): The template's language code. Default: 'en_US'.
        reengage_template (None | str | Unset): An approved message template on this number's business account. Sent to
            a member whose 24-hour window has closed, holding the message itself until they reply. Unset, such a message is
            refused.
    """

    language: str | Unset = "en_US"
    reengage_template: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        language = self.language

        reengage_template: None | str | Unset
        if isinstance(self.reengage_template, Unset):
            reengage_template = UNSET
        else:
            reengage_template = self.reengage_template

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if language is not UNSET:
            field_dict["language"] = language
        if reengage_template is not UNSET:
            field_dict["reengage_template"] = reengage_template

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        language = d.pop("language", UNSET)

        def _parse_reengage_template(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        reengage_template = _parse_reengage_template(d.pop("reengage_template", UNSET))

        surface_whats_app_config_input = cls(
            language=language,
            reengage_template=reengage_template,
        )

        return surface_whats_app_config_input
