from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.link_state import LinkState
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.telegram_account_response import TelegramAccountResponse


T = TypeVar("T", bound="IdentityLinkStatusResponse")


@_attrs_define
class IdentityLinkStatusResponse:
    """
    Attributes:
        state (LinkState):
        account (None | TelegramAccountResponse | Unset):
    """

    state: LinkState
    account: None | TelegramAccountResponse | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.telegram_account_response import TelegramAccountResponse

        state = self.state.value

        account: dict[str, Any] | None | Unset
        if isinstance(self.account, Unset):
            account = UNSET
        elif isinstance(self.account, TelegramAccountResponse):
            account = self.account.to_dict()
        else:
            account = self.account

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "state": state,
            }
        )
        if account is not UNSET:
            field_dict["account"] = account

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.telegram_account_response import TelegramAccountResponse

        d = dict(src_dict)
        state = LinkState(d.pop("state"))

        def _parse_account(data: object) -> None | TelegramAccountResponse | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                account_type_0 = TelegramAccountResponse.from_dict(data)

                return account_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | TelegramAccountResponse | Unset, data)

        account = _parse_account(d.pop("account", UNSET))

        identity_link_status_response = cls(
            state=state,
            account=account,
        )

        identity_link_status_response.additional_properties = d
        return identity_link_status_response

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
