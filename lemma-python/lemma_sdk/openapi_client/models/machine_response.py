from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.machine_response_access import MachineResponseAccess
from ..models.machine_response_kind import MachineResponseKind
from ..models.machine_response_status import MachineResponseStatus
from ..models.machine_response_template import MachineResponseTemplate
from ..models.machine_response_visibility import MachineResponseVisibility
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.machine_capabilities_model import MachineCapabilitiesModel


T = TypeVar("T", bound="MachineResponse")


@_attrs_define
class MachineResponse:
    """
    Attributes:
        access (MachineResponseAccess): What the caller may do with it.
        capabilities (MachineCapabilitiesModel):
        display_name (str):
        fabric (str):
        home (str):
        id (UUID):
        kind (MachineResponseKind):
        name (str):
        ref (str): How to name this machine in a call: `me`, `pod:<pod-id>/<name>` or `mine:<name>`. In a URL path use
            `:` for the slash.
        run_slots (int):
        size (str): `<vcpu>x<memory GB>`, e.g. `2x4`.
        status (MachineResponseStatus):
        template (MachineResponseTemplate):
        visibility (MachineResponseVisibility):
        compute_units_this_month (float | None | Unset): Compute used this calendar month (1 CU = one hour at 2x4).
        last_used_at (datetime.datetime | None | Unset):
        pod_id (None | Unset | UUID):
    """

    access: MachineResponseAccess
    capabilities: MachineCapabilitiesModel
    display_name: str
    fabric: str
    home: str
    id: UUID
    kind: MachineResponseKind
    name: str
    ref: str
    run_slots: int
    size: str
    status: MachineResponseStatus
    template: MachineResponseTemplate
    visibility: MachineResponseVisibility
    compute_units_this_month: float | None | Unset = UNSET
    last_used_at: datetime.datetime | None | Unset = UNSET
    pod_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        access = self.access.value

        capabilities = self.capabilities.to_dict()

        display_name = self.display_name

        fabric = self.fabric

        home = self.home

        id = str(self.id)

        kind = self.kind.value

        name = self.name

        ref = self.ref

        run_slots = self.run_slots

        size = self.size

        status = self.status.value

        template = self.template.value

        visibility = self.visibility.value

        compute_units_this_month: float | None | Unset
        if isinstance(self.compute_units_this_month, Unset):
            compute_units_this_month = UNSET
        else:
            compute_units_this_month = self.compute_units_this_month

        last_used_at: None | str | Unset
        if isinstance(self.last_used_at, Unset):
            last_used_at = UNSET
        elif isinstance(self.last_used_at, datetime.datetime):
            last_used_at = self.last_used_at.isoformat()
        else:
            last_used_at = self.last_used_at

        pod_id: None | str | Unset
        if isinstance(self.pod_id, Unset):
            pod_id = UNSET
        elif isinstance(self.pod_id, UUID):
            pod_id = str(self.pod_id)
        else:
            pod_id = self.pod_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "access": access,
                "capabilities": capabilities,
                "display_name": display_name,
                "fabric": fabric,
                "home": home,
                "id": id,
                "kind": kind,
                "name": name,
                "ref": ref,
                "run_slots": run_slots,
                "size": size,
                "status": status,
                "template": template,
                "visibility": visibility,
            }
        )
        if compute_units_this_month is not UNSET:
            field_dict["compute_units_this_month"] = compute_units_this_month
        if last_used_at is not UNSET:
            field_dict["last_used_at"] = last_used_at
        if pod_id is not UNSET:
            field_dict["pod_id"] = pod_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.machine_capabilities_model import MachineCapabilitiesModel

        d = dict(src_dict)
        access = MachineResponseAccess(d.pop("access"))

        capabilities = MachineCapabilitiesModel.from_dict(d.pop("capabilities"))

        display_name = d.pop("display_name")

        fabric = d.pop("fabric")

        home = d.pop("home")

        id = UUID(d.pop("id"))

        kind = MachineResponseKind(d.pop("kind"))

        name = d.pop("name")

        ref = d.pop("ref")

        run_slots = d.pop("run_slots")

        size = d.pop("size")

        status = MachineResponseStatus(d.pop("status"))

        template = MachineResponseTemplate(d.pop("template"))

        visibility = MachineResponseVisibility(d.pop("visibility"))

        def _parse_compute_units_this_month(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        compute_units_this_month = _parse_compute_units_this_month(
            d.pop("compute_units_this_month", UNSET)
        )

        def _parse_last_used_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_used_at_type_0 = isoparse(data)

                return last_used_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        last_used_at = _parse_last_used_at(d.pop("last_used_at", UNSET))

        def _parse_pod_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                pod_id_type_0 = UUID(data)

                return pod_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        pod_id = _parse_pod_id(d.pop("pod_id", UNSET))

        machine_response = cls(
            access=access,
            capabilities=capabilities,
            display_name=display_name,
            fabric=fabric,
            home=home,
            id=id,
            kind=kind,
            name=name,
            ref=ref,
            run_slots=run_slots,
            size=size,
            status=status,
            template=template,
            visibility=visibility,
            compute_units_this_month=compute_units_this_month,
            last_used_at=last_used_at,
            pod_id=pod_id,
        )

        machine_response.additional_properties = d
        return machine_response

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
