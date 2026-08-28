from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="SurfaceSlackConfigInput")


@_attrs_define
class SurfaceSlackConfigInput:
    """The Slack settings a *caller* owns.

    Not the per-person DM agent map: that is written from inside Slack — each
    person picks their own in the App Home — so it is readable here and never
    writable, which keeps one editor from reassigning everybody.

    ``dedicated_to_agent`` is the caller's, though, and has to be: it says this
    app was made as one agent's own bot, which is a fact about why the app
    exists and cannot be read off the surface. Setting it is what withdraws the
    per-person choice, so it is the one Slack setting that decides whether the
    other is offered at all.

        Attributes:
            app_name (None | str | Unset):
            dedicated_to_agent (bool | Unset):  Default: False.
    """

    app_name: None | str | Unset = UNSET
    dedicated_to_agent: bool | Unset = False

    def to_dict(self) -> dict[str, Any]:
        app_name: None | str | Unset
        if isinstance(self.app_name, Unset):
            app_name = UNSET
        else:
            app_name = self.app_name

        dedicated_to_agent = self.dedicated_to_agent

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if app_name is not UNSET:
            field_dict["app_name"] = app_name
        if dedicated_to_agent is not UNSET:
            field_dict["dedicated_to_agent"] = dedicated_to_agent

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

        dedicated_to_agent = d.pop("dedicated_to_agent", UNSET)

        surface_slack_config_input = cls(
            app_name=app_name,
            dedicated_to_agent=dedicated_to_agent,
        )

        return surface_slack_config_input
