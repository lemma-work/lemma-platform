from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ConnectorChoice")


@_attrs_define
class ConnectorChoice:
    """What the importer chose for one bundled connector.

    Leave it all out to take the plan's default installation and share no
    account. ``auth_config_id`` and ``create_installation`` are alternatives.

        Attributes:
            auth_config_id (None | Unset | UUID): One of the plan's `installations` for this connector.
            create_installation (bool | Unset): Install Lemma's own app for the connector in your organization and use it.
                Offered only where the plan says `can_create_installation`. Default: False.
            share_account_id (None | Unset | UUID): SHARED only: one of your own accounts (the plan's `account_options`) to
                share with the pod for this connector to run as. Left out, the connector imports needing an account.
    """

    auth_config_id: None | Unset | UUID = UNSET
    create_installation: bool | Unset = False
    share_account_id: None | Unset | UUID = UNSET

    def to_dict(self) -> dict[str, Any]:
        auth_config_id: None | str | Unset
        if isinstance(self.auth_config_id, Unset):
            auth_config_id = UNSET
        elif isinstance(self.auth_config_id, UUID):
            auth_config_id = str(self.auth_config_id)
        else:
            auth_config_id = self.auth_config_id

        create_installation = self.create_installation

        share_account_id: None | str | Unset
        if isinstance(self.share_account_id, Unset):
            share_account_id = UNSET
        elif isinstance(self.share_account_id, UUID):
            share_account_id = str(self.share_account_id)
        else:
            share_account_id = self.share_account_id

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if auth_config_id is not UNSET:
            field_dict["auth_config_id"] = auth_config_id
        if create_installation is not UNSET:
            field_dict["create_installation"] = create_installation
        if share_account_id is not UNSET:
            field_dict["share_account_id"] = share_account_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_auth_config_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                auth_config_id_type_0 = UUID(data)

                return auth_config_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        auth_config_id = _parse_auth_config_id(d.pop("auth_config_id", UNSET))

        create_installation = d.pop("create_installation", UNSET)

        def _parse_share_account_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                share_account_id_type_0 = UUID(data)

                return share_account_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        share_account_id = _parse_share_account_id(d.pop("share_account_id", UNSET))

        connector_choice = cls(
            auth_config_id=auth_config_id,
            create_installation=create_installation,
            share_account_id=share_account_id,
        )

        return connector_choice
