from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.browser_status_response_presets_type_0 import (
        BrowserStatusResponsePresetsType0,
    )
    from ..models.browser_status_response_watching_item import (
        BrowserStatusResponseWatchingItem,
    )


T = TypeVar("T", bound="BrowserStatusResponse")


@_attrs_define
class BrowserStatusResponse:
    """What the pane can say without waking anything.

    `asleep` the computer is paused or was never started; `stopped` it is up but
    the browser is not (the resting state after five idle minutes); `running` a
    browser is there now; `unavailable` the relay did not answer, which on an
    older image is permanent until it is replaced; `unsupported` this fabric
    cannot reach a port at all.

        Attributes:
            state (str):
            agent_driving (bool | Unset):  Default: False.
            density (int | None | Unset):
            detail (None | str | Unset):
            lease (None | str | Unset):
            presets (BrowserStatusResponsePresetsType0 | None | Unset):
            size (None | str | Unset):
            transport (None | str | Unset):
            watching (list[BrowserStatusResponseWatchingItem] | Unset):
    """

    state: str
    agent_driving: bool | Unset = False
    density: int | None | Unset = UNSET
    detail: None | str | Unset = UNSET
    lease: None | str | Unset = UNSET
    presets: BrowserStatusResponsePresetsType0 | None | Unset = UNSET
    size: None | str | Unset = UNSET
    transport: None | str | Unset = UNSET
    watching: list[BrowserStatusResponseWatchingItem] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.browser_status_response_presets_type_0 import (
            BrowserStatusResponsePresetsType0,
        )

        state = self.state

        agent_driving = self.agent_driving

        density: int | None | Unset
        if isinstance(self.density, Unset):
            density = UNSET
        else:
            density = self.density

        detail: None | str | Unset
        if isinstance(self.detail, Unset):
            detail = UNSET
        else:
            detail = self.detail

        lease: None | str | Unset
        if isinstance(self.lease, Unset):
            lease = UNSET
        else:
            lease = self.lease

        presets: dict[str, Any] | None | Unset
        if isinstance(self.presets, Unset):
            presets = UNSET
        elif isinstance(self.presets, BrowserStatusResponsePresetsType0):
            presets = self.presets.to_dict()
        else:
            presets = self.presets

        size: None | str | Unset
        if isinstance(self.size, Unset):
            size = UNSET
        else:
            size = self.size

        transport: None | str | Unset
        if isinstance(self.transport, Unset):
            transport = UNSET
        else:
            transport = self.transport

        watching: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.watching, Unset):
            watching = []
            for watching_item_data in self.watching:
                watching_item = watching_item_data.to_dict()
                watching.append(watching_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "state": state,
            }
        )
        if agent_driving is not UNSET:
            field_dict["agent_driving"] = agent_driving
        if density is not UNSET:
            field_dict["density"] = density
        if detail is not UNSET:
            field_dict["detail"] = detail
        if lease is not UNSET:
            field_dict["lease"] = lease
        if presets is not UNSET:
            field_dict["presets"] = presets
        if size is not UNSET:
            field_dict["size"] = size
        if transport is not UNSET:
            field_dict["transport"] = transport
        if watching is not UNSET:
            field_dict["watching"] = watching

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.browser_status_response_presets_type_0 import (
            BrowserStatusResponsePresetsType0,
        )
        from ..models.browser_status_response_watching_item import (
            BrowserStatusResponseWatchingItem,
        )

        d = dict(src_dict)
        state = d.pop("state")

        agent_driving = d.pop("agent_driving", UNSET)

        def _parse_density(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        density = _parse_density(d.pop("density", UNSET))

        def _parse_detail(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        detail = _parse_detail(d.pop("detail", UNSET))

        def _parse_lease(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        lease = _parse_lease(d.pop("lease", UNSET))

        def _parse_presets(
            data: object,
        ) -> BrowserStatusResponsePresetsType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                presets_type_0 = BrowserStatusResponsePresetsType0.from_dict(data)

                return presets_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(BrowserStatusResponsePresetsType0 | None | Unset, data)

        presets = _parse_presets(d.pop("presets", UNSET))

        def _parse_size(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        size = _parse_size(d.pop("size", UNSET))

        def _parse_transport(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        transport = _parse_transport(d.pop("transport", UNSET))

        _watching = d.pop("watching", UNSET)
        watching: list[BrowserStatusResponseWatchingItem] | Unset = UNSET
        if _watching is not UNSET:
            watching = []
            for watching_item_data in _watching:
                watching_item = BrowserStatusResponseWatchingItem.from_dict(
                    watching_item_data
                )

                watching.append(watching_item)

        browser_status_response = cls(
            state=state,
            agent_driving=agent_driving,
            density=density,
            detail=detail,
            lease=lease,
            presets=presets,
            size=size,
            transport=transport,
            watching=watching,
        )

        browser_status_response.additional_properties = d
        return browser_status_response

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
