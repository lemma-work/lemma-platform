from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.pod_connector_mode import PodConnectorMode
from ..models.pod_connector_source import PodConnectorSource
from ..models.pod_connector_status import PodConnectorStatus
from ..models.pod_connector_visibility import PodConnectorVisibility
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.pod_connector_coverage import PodConnectorCoverage
    from ..models.shared_account_summary import SharedAccountSummary


T = TypeVar("T", bound="PodConnectorResponse")


@_attrs_define
class PodConnectorResponse:
    """
    Attributes:
        auth_config_id (UUID):
        created_at (datetime.datetime):
        id (UUID):
        mode (PodConnectorMode):
        pod_id (UUID):
        required (bool):
        slug (str):
        status (PodConnectorStatus):
        updated_at (datetime.datetime):
        visibility (PodConnectorVisibility):
        can_use (bool | Unset): Whether the caller may run operations through it: what decides if connecting their own
            account would let them use it. False for a viewer, and for anyone a RESTRICTED connector leaves out. Default:
            False.
        connector_id (None | str | Unset):
        created_by_user_id (None | Unset | UUID):
        installation_name (None | str | Unset):
        member_coverage (None | PodConnectorCoverage | Unset): Not filled in on listings, which stay cheap: read it from
            `GET /pods/{pod_id}/connectors/{slug}/coverage`. Kept so a client reading it gets null rather than a missing
            key.
        purpose (None | str | Unset):
        shared_account (None | SharedAccountSummary | Unset):
        shared_pod_account_id (None | Unset | UUID):
        source (PodConnectorSource | Unset):
    """

    auth_config_id: UUID
    created_at: datetime.datetime
    id: UUID
    mode: PodConnectorMode
    pod_id: UUID
    required: bool
    slug: str
    status: PodConnectorStatus
    updated_at: datetime.datetime
    visibility: PodConnectorVisibility
    can_use: bool | Unset = False
    connector_id: None | str | Unset = UNSET
    created_by_user_id: None | Unset | UUID = UNSET
    installation_name: None | str | Unset = UNSET
    member_coverage: None | PodConnectorCoverage | Unset = UNSET
    purpose: None | str | Unset = UNSET
    shared_account: None | SharedAccountSummary | Unset = UNSET
    shared_pod_account_id: None | Unset | UUID = UNSET
    source: PodConnectorSource | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.pod_connector_coverage import PodConnectorCoverage
        from ..models.shared_account_summary import SharedAccountSummary

        auth_config_id = str(self.auth_config_id)

        created_at = self.created_at.isoformat()

        id = str(self.id)

        mode = self.mode.value

        pod_id = str(self.pod_id)

        required = self.required

        slug = self.slug

        status = self.status.value

        updated_at = self.updated_at.isoformat()

        visibility = self.visibility.value

        can_use = self.can_use

        connector_id: None | str | Unset
        if isinstance(self.connector_id, Unset):
            connector_id = UNSET
        else:
            connector_id = self.connector_id

        created_by_user_id: None | str | Unset
        if isinstance(self.created_by_user_id, Unset):
            created_by_user_id = UNSET
        elif isinstance(self.created_by_user_id, UUID):
            created_by_user_id = str(self.created_by_user_id)
        else:
            created_by_user_id = self.created_by_user_id

        installation_name: None | str | Unset
        if isinstance(self.installation_name, Unset):
            installation_name = UNSET
        else:
            installation_name = self.installation_name

        member_coverage: dict[str, Any] | None | Unset
        if isinstance(self.member_coverage, Unset):
            member_coverage = UNSET
        elif isinstance(self.member_coverage, PodConnectorCoverage):
            member_coverage = self.member_coverage.to_dict()
        else:
            member_coverage = self.member_coverage

        purpose: None | str | Unset
        if isinstance(self.purpose, Unset):
            purpose = UNSET
        else:
            purpose = self.purpose

        shared_account: dict[str, Any] | None | Unset
        if isinstance(self.shared_account, Unset):
            shared_account = UNSET
        elif isinstance(self.shared_account, SharedAccountSummary):
            shared_account = self.shared_account.to_dict()
        else:
            shared_account = self.shared_account

        shared_pod_account_id: None | str | Unset
        if isinstance(self.shared_pod_account_id, Unset):
            shared_pod_account_id = UNSET
        elif isinstance(self.shared_pod_account_id, UUID):
            shared_pod_account_id = str(self.shared_pod_account_id)
        else:
            shared_pod_account_id = self.shared_pod_account_id

        source: str | Unset = UNSET
        if not isinstance(self.source, Unset):
            source = self.source.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "auth_config_id": auth_config_id,
                "created_at": created_at,
                "id": id,
                "mode": mode,
                "pod_id": pod_id,
                "required": required,
                "slug": slug,
                "status": status,
                "updated_at": updated_at,
                "visibility": visibility,
            }
        )
        if can_use is not UNSET:
            field_dict["can_use"] = can_use
        if connector_id is not UNSET:
            field_dict["connector_id"] = connector_id
        if created_by_user_id is not UNSET:
            field_dict["created_by_user_id"] = created_by_user_id
        if installation_name is not UNSET:
            field_dict["installation_name"] = installation_name
        if member_coverage is not UNSET:
            field_dict["member_coverage"] = member_coverage
        if purpose is not UNSET:
            field_dict["purpose"] = purpose
        if shared_account is not UNSET:
            field_dict["shared_account"] = shared_account
        if shared_pod_account_id is not UNSET:
            field_dict["shared_pod_account_id"] = shared_pod_account_id
        if source is not UNSET:
            field_dict["source"] = source

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.pod_connector_coverage import PodConnectorCoverage
        from ..models.shared_account_summary import SharedAccountSummary

        d = dict(src_dict)
        auth_config_id = UUID(d.pop("auth_config_id"))

        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        mode = PodConnectorMode(d.pop("mode"))

        pod_id = UUID(d.pop("pod_id"))

        required = d.pop("required")

        slug = d.pop("slug")

        status = PodConnectorStatus(d.pop("status"))

        updated_at = isoparse(d.pop("updated_at"))

        visibility = PodConnectorVisibility(d.pop("visibility"))

        can_use = d.pop("can_use", UNSET)

        def _parse_connector_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        connector_id = _parse_connector_id(d.pop("connector_id", UNSET))

        def _parse_created_by_user_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                created_by_user_id_type_0 = UUID(data)

                return created_by_user_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        created_by_user_id = _parse_created_by_user_id(
            d.pop("created_by_user_id", UNSET)
        )

        def _parse_installation_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        installation_name = _parse_installation_name(d.pop("installation_name", UNSET))

        def _parse_member_coverage(data: object) -> None | PodConnectorCoverage | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                member_coverage_type_0 = PodConnectorCoverage.from_dict(data)

                return member_coverage_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PodConnectorCoverage | Unset, data)

        member_coverage = _parse_member_coverage(d.pop("member_coverage", UNSET))

        def _parse_purpose(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        purpose = _parse_purpose(d.pop("purpose", UNSET))

        def _parse_shared_account(data: object) -> None | SharedAccountSummary | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                shared_account_type_0 = SharedAccountSummary.from_dict(data)

                return shared_account_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | SharedAccountSummary | Unset, data)

        shared_account = _parse_shared_account(d.pop("shared_account", UNSET))

        def _parse_shared_pod_account_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                shared_pod_account_id_type_0 = UUID(data)

                return shared_pod_account_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        shared_pod_account_id = _parse_shared_pod_account_id(
            d.pop("shared_pod_account_id", UNSET)
        )

        _source = d.pop("source", UNSET)
        source: PodConnectorSource | Unset
        if isinstance(_source, Unset):
            source = UNSET
        else:
            source = PodConnectorSource(_source)

        pod_connector_response = cls(
            auth_config_id=auth_config_id,
            created_at=created_at,
            id=id,
            mode=mode,
            pod_id=pod_id,
            required=required,
            slug=slug,
            status=status,
            updated_at=updated_at,
            visibility=visibility,
            can_use=can_use,
            connector_id=connector_id,
            created_by_user_id=created_by_user_id,
            installation_name=installation_name,
            member_coverage=member_coverage,
            purpose=purpose,
            shared_account=shared_account,
            shared_pod_account_id=shared_pod_account_id,
            source=source,
        )

        pod_connector_response.additional_properties = d
        return pod_connector_response

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
