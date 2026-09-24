from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.installation_response_deployment import InstallationResponseDeployment
from ..models.installation_response_signup_mode import InstallationResponseSignupMode

T = TypeVar("T", bound="InstallationResponse")


@_attrs_define
class InstallationResponse:
    """What this installation is, and what the caller is to it.

    Attributes:
        deployment (InstallationResponseDeployment): ``desktop`` for a Lemma Desktop installation on one person's
            computer, ``server`` for hosted and self-hosted deployments.
        is_owner (bool): Whether the caller is this installation's owner: the first account created on a Desktop
            installation. Always false on ``server``.
        signup_mode (InstallationResponseSignupMode): Who may create a new account on this installation.
    """

    deployment: InstallationResponseDeployment
    is_owner: bool
    signup_mode: InstallationResponseSignupMode
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        deployment = self.deployment.value

        is_owner = self.is_owner

        signup_mode = self.signup_mode.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "deployment": deployment,
                "is_owner": is_owner,
                "signup_mode": signup_mode,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        deployment = InstallationResponseDeployment(d.pop("deployment"))

        is_owner = d.pop("is_owner")

        signup_mode = InstallationResponseSignupMode(d.pop("signup_mode"))

        installation_response = cls(
            deployment=deployment,
            is_owner=is_owner,
            signup_mode=signup_mode,
        )

        installation_response.additional_properties = d
        return installation_response

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
