from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.pod_connector_mode import PodConnectorMode
from ..models.pod_connector_status import PodConnectorStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.connection_coverage import ConnectionCoverage
    from ..models.connection_me import ConnectionMe
    from ..models.connection_runs_as import ConnectionRunsAs


T = TypeVar("T", bound="PodConnectionItem")


@_attrs_define
class PodConnectionItem:
    """
    Attributes:
        auth_config_id (UUID): The organization installation; what a connect request names.
        me (ConnectionMe):
        mode (PodConnectorMode):
        runs_as (ConnectionRunsAs): Who a call by the caller runs as, in words the page can show.
        slug (str):
        status (PodConnectorStatus):
        connector_id (None | str | Unset):
        coverage (ConnectionCoverage | None | Unset):
        icon (None | str | Unset):
        installation_name (None | str | Unset):
        purpose (None | str | Unset):
        required (bool | Unset):  Default: False.
        title (None | str | Unset):
    """

    auth_config_id: UUID
    me: ConnectionMe
    mode: PodConnectorMode
    runs_as: ConnectionRunsAs
    slug: str
    status: PodConnectorStatus
    connector_id: None | str | Unset = UNSET
    coverage: ConnectionCoverage | None | Unset = UNSET
    icon: None | str | Unset = UNSET
    installation_name: None | str | Unset = UNSET
    purpose: None | str | Unset = UNSET
    required: bool | Unset = False
    title: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.connection_coverage import ConnectionCoverage

        auth_config_id = str(self.auth_config_id)

        me = self.me.to_dict()

        mode = self.mode.value

        runs_as = self.runs_as.to_dict()

        slug = self.slug

        status = self.status.value

        connector_id: None | str | Unset
        if isinstance(self.connector_id, Unset):
            connector_id = UNSET
        else:
            connector_id = self.connector_id

        coverage: dict[str, Any] | None | Unset
        if isinstance(self.coverage, Unset):
            coverage = UNSET
        elif isinstance(self.coverage, ConnectionCoverage):
            coverage = self.coverage.to_dict()
        else:
            coverage = self.coverage

        icon: None | str | Unset
        if isinstance(self.icon, Unset):
            icon = UNSET
        else:
            icon = self.icon

        installation_name: None | str | Unset
        if isinstance(self.installation_name, Unset):
            installation_name = UNSET
        else:
            installation_name = self.installation_name

        purpose: None | str | Unset
        if isinstance(self.purpose, Unset):
            purpose = UNSET
        else:
            purpose = self.purpose

        required = self.required

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "auth_config_id": auth_config_id,
                "me": me,
                "mode": mode,
                "runs_as": runs_as,
                "slug": slug,
                "status": status,
            }
        )
        if connector_id is not UNSET:
            field_dict["connector_id"] = connector_id
        if coverage is not UNSET:
            field_dict["coverage"] = coverage
        if icon is not UNSET:
            field_dict["icon"] = icon
        if installation_name is not UNSET:
            field_dict["installation_name"] = installation_name
        if purpose is not UNSET:
            field_dict["purpose"] = purpose
        if required is not UNSET:
            field_dict["required"] = required
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.connection_coverage import ConnectionCoverage
        from ..models.connection_me import ConnectionMe
        from ..models.connection_runs_as import ConnectionRunsAs

        d = dict(src_dict)
        auth_config_id = UUID(d.pop("auth_config_id"))

        me = ConnectionMe.from_dict(d.pop("me"))

        mode = PodConnectorMode(d.pop("mode"))

        runs_as = ConnectionRunsAs.from_dict(d.pop("runs_as"))

        slug = d.pop("slug")

        status = PodConnectorStatus(d.pop("status"))

        def _parse_connector_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        connector_id = _parse_connector_id(d.pop("connector_id", UNSET))

        def _parse_coverage(data: object) -> ConnectionCoverage | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                coverage_type_0 = ConnectionCoverage.from_dict(data)

                return coverage_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(ConnectionCoverage | None | Unset, data)

        coverage = _parse_coverage(d.pop("coverage", UNSET))

        def _parse_icon(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        icon = _parse_icon(d.pop("icon", UNSET))

        def _parse_installation_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        installation_name = _parse_installation_name(d.pop("installation_name", UNSET))

        def _parse_purpose(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        purpose = _parse_purpose(d.pop("purpose", UNSET))

        required = d.pop("required", UNSET)

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        pod_connection_item = cls(
            auth_config_id=auth_config_id,
            me=me,
            mode=mode,
            runs_as=runs_as,
            slug=slug,
            status=status,
            connector_id=connector_id,
            coverage=coverage,
            icon=icon,
            installation_name=installation_name,
            purpose=purpose,
            required=required,
            title=title,
        )

        pod_connection_item.additional_properties = d
        return pod_connection_item

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
