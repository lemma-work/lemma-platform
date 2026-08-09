from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.surface_channel_route_input import SurfaceChannelRouteInput
    from ..models.surface_identity_config_input import SurfaceIdentityConfigInput
    from ..models.surface_send_policy_config import SurfaceSendPolicyConfig
    from ..models.surface_slack_config_input import SurfaceSlackConfigInput
    from ..models.surface_telegram_config_input import SurfaceTelegramConfigInput


T = TypeVar("T", bound="SurfaceBehaviorConfigInput")


@_attrs_define
class SurfaceBehaviorConfigInput:
    """
    Attributes:
        channels (list[SurfaceChannelRouteInput] | Unset):
        dm_conversation_reset_after_hours (int | Unset):  Default: 24.
        identity (SurfaceIdentityConfigInput | Unset):
        send_policy (SurfaceSendPolicyConfig | Unset): Proactive-send controls. Mirrored across request and response.
        slack (SurfaceSlackConfigInput | Unset): The Slack settings a *caller* owns.

            Only ``app_name``. The per-person DM agent map is written from inside Slack
            — each person picks their own in the App Home — so it is readable here and
            never writable, which keeps one editor from reassigning everybody.
        telegram (SurfaceTelegramConfigInput | Unset): Selects the pod app exposed as this bot's Telegram Mini App.
    """

    channels: list[SurfaceChannelRouteInput] | Unset = UNSET
    dm_conversation_reset_after_hours: int | Unset = 24
    identity: SurfaceIdentityConfigInput | Unset = UNSET
    send_policy: SurfaceSendPolicyConfig | Unset = UNSET
    slack: SurfaceSlackConfigInput | Unset = UNSET
    telegram: SurfaceTelegramConfigInput | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        channels: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.channels, Unset):
            channels = []
            for channels_item_data in self.channels:
                channels_item = channels_item_data.to_dict()
                channels.append(channels_item)

        dm_conversation_reset_after_hours = self.dm_conversation_reset_after_hours

        identity: dict[str, Any] | Unset = UNSET
        if not isinstance(self.identity, Unset):
            identity = self.identity.to_dict()

        send_policy: dict[str, Any] | Unset = UNSET
        if not isinstance(self.send_policy, Unset):
            send_policy = self.send_policy.to_dict()

        slack: dict[str, Any] | Unset = UNSET
        if not isinstance(self.slack, Unset):
            slack = self.slack.to_dict()

        telegram: dict[str, Any] | Unset = UNSET
        if not isinstance(self.telegram, Unset):
            telegram = self.telegram.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if channels is not UNSET:
            field_dict["channels"] = channels
        if dm_conversation_reset_after_hours is not UNSET:
            field_dict["dm_conversation_reset_after_hours"] = (
                dm_conversation_reset_after_hours
            )
        if identity is not UNSET:
            field_dict["identity"] = identity
        if send_policy is not UNSET:
            field_dict["send_policy"] = send_policy
        if slack is not UNSET:
            field_dict["slack"] = slack
        if telegram is not UNSET:
            field_dict["telegram"] = telegram

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.surface_channel_route_input import SurfaceChannelRouteInput
        from ..models.surface_identity_config_input import SurfaceIdentityConfigInput
        from ..models.surface_send_policy_config import SurfaceSendPolicyConfig
        from ..models.surface_slack_config_input import SurfaceSlackConfigInput
        from ..models.surface_telegram_config_input import SurfaceTelegramConfigInput

        d = dict(src_dict)
        _channels = d.pop("channels", UNSET)
        channels: list[SurfaceChannelRouteInput] | Unset = UNSET
        if _channels is not UNSET:
            channels = []
            for channels_item_data in _channels:
                channels_item = SurfaceChannelRouteInput.from_dict(channels_item_data)

                channels.append(channels_item)

        dm_conversation_reset_after_hours = d.pop(
            "dm_conversation_reset_after_hours", UNSET
        )

        _identity = d.pop("identity", UNSET)
        identity: SurfaceIdentityConfigInput | Unset
        if isinstance(_identity, Unset):
            identity = UNSET
        else:
            identity = SurfaceIdentityConfigInput.from_dict(_identity)

        _send_policy = d.pop("send_policy", UNSET)
        send_policy: SurfaceSendPolicyConfig | Unset
        if isinstance(_send_policy, Unset):
            send_policy = UNSET
        else:
            send_policy = SurfaceSendPolicyConfig.from_dict(_send_policy)

        _slack = d.pop("slack", UNSET)
        slack: SurfaceSlackConfigInput | Unset
        if isinstance(_slack, Unset):
            slack = UNSET
        else:
            slack = SurfaceSlackConfigInput.from_dict(_slack)

        _telegram = d.pop("telegram", UNSET)
        telegram: SurfaceTelegramConfigInput | Unset
        if isinstance(_telegram, Unset):
            telegram = UNSET
        else:
            telegram = SurfaceTelegramConfigInput.from_dict(_telegram)

        surface_behavior_config_input = cls(
            channels=channels,
            dm_conversation_reset_after_hours=dm_conversation_reset_after_hours,
            identity=identity,
            send_policy=send_policy,
            slack=slack,
            telegram=telegram,
        )

        return surface_behavior_config_input
