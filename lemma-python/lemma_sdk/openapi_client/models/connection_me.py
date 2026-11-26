from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.connection_standing import ConnectionStanding
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.connection_account import ConnectionAccount
    from ..models.existing_account import ExistingAccount


T = TypeVar("T", bound="ConnectionMe")


@_attrs_define
class ConnectionMe:
    """
    Attributes:
        standing (ConnectionStanding): What the caller can do about a connector right now.

            By mode: PER_MEMBER is ``ready``, ``connect`` (no account of theirs on the
            installation anywhere), ``use_existing`` (one in the organization, not in
            this pod yet) or ``reconnect``; SHARED and APP are ``ready`` or ``waiting``
            (no account shared; the App not installed); any mode is ``waiting`` when
            its installation is disabled, and ``not_allowed`` without ``connector.use``.
        account (ConnectionAccount | None | Unset):
        existing (ExistingAccount | None | Unset): Set with `use_existing`: the account to add.
    """

    standing: ConnectionStanding
    account: ConnectionAccount | None | Unset = UNSET
    existing: ExistingAccount | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.connection_account import ConnectionAccount
        from ..models.existing_account import ExistingAccount

        standing = self.standing.value

        account: dict[str, Any] | None | Unset
        if isinstance(self.account, Unset):
            account = UNSET
        elif isinstance(self.account, ConnectionAccount):
            account = self.account.to_dict()
        else:
            account = self.account

        existing: dict[str, Any] | None | Unset
        if isinstance(self.existing, Unset):
            existing = UNSET
        elif isinstance(self.existing, ExistingAccount):
            existing = self.existing.to_dict()
        else:
            existing = self.existing

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "standing": standing,
            }
        )
        if account is not UNSET:
            field_dict["account"] = account
        if existing is not UNSET:
            field_dict["existing"] = existing

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.connection_account import ConnectionAccount
        from ..models.existing_account import ExistingAccount

        d = dict(src_dict)
        standing = ConnectionStanding(d.pop("standing"))

        def _parse_account(data: object) -> ConnectionAccount | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                account_type_0 = ConnectionAccount.from_dict(data)

                return account_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(ConnectionAccount | None | Unset, data)

        account = _parse_account(d.pop("account", UNSET))

        def _parse_existing(data: object) -> ExistingAccount | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                existing_type_0 = ExistingAccount.from_dict(data)

                return existing_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(ExistingAccount | None | Unset, data)

        existing = _parse_existing(d.pop("existing", UNSET))

        connection_me = cls(
            standing=standing,
            account=account,
            existing=existing,
        )

        connection_me.additional_properties = d
        return connection_me

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
