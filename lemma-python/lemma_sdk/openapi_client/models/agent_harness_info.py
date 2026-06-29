from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.harness_kind import HarnessKind
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.runtime_model_catalog_entry import RuntimeModelCatalogEntry


T = TypeVar("T", bound="AgentHarnessInfo")


@_attrs_define
class AgentHarnessInfo:
    """
    Attributes:
        display_name (str):
        harness_kind (HarnessKind): Runtime framework used to execute an agent.
        availability_status (None | str | Unset):
        available (bool | Unset):  Default: True.
        daemon_display_name (None | str | Unset):
        daemon_id (None | Unset | UUID):
        daemon_status (None | str | Unset):
        model_catalog (list[RuntimeModelCatalogEntry] | Unset):
        models (list[str] | Unset):
    """

    display_name: str
    harness_kind: HarnessKind
    availability_status: None | str | Unset = UNSET
    available: bool | Unset = True
    daemon_display_name: None | str | Unset = UNSET
    daemon_id: None | Unset | UUID = UNSET
    daemon_status: None | str | Unset = UNSET
    model_catalog: list[RuntimeModelCatalogEntry] | Unset = UNSET
    models: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        display_name = self.display_name

        harness_kind = self.harness_kind.value

        availability_status: None | str | Unset
        if isinstance(self.availability_status, Unset):
            availability_status = UNSET
        else:
            availability_status = self.availability_status

        available = self.available

        daemon_display_name: None | str | Unset
        if isinstance(self.daemon_display_name, Unset):
            daemon_display_name = UNSET
        else:
            daemon_display_name = self.daemon_display_name

        daemon_id: None | str | Unset
        if isinstance(self.daemon_id, Unset):
            daemon_id = UNSET
        elif isinstance(self.daemon_id, UUID):
            daemon_id = str(self.daemon_id)
        else:
            daemon_id = self.daemon_id

        daemon_status: None | str | Unset
        if isinstance(self.daemon_status, Unset):
            daemon_status = UNSET
        else:
            daemon_status = self.daemon_status

        model_catalog: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.model_catalog, Unset):
            model_catalog = []
            for model_catalog_item_data in self.model_catalog:
                model_catalog_item = model_catalog_item_data.to_dict()
                model_catalog.append(model_catalog_item)

        models: list[str] | Unset = UNSET
        if not isinstance(self.models, Unset):
            models = self.models

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "display_name": display_name,
                "harness_kind": harness_kind,
            }
        )
        if availability_status is not UNSET:
            field_dict["availability_status"] = availability_status
        if available is not UNSET:
            field_dict["available"] = available
        if daemon_display_name is not UNSET:
            field_dict["daemon_display_name"] = daemon_display_name
        if daemon_id is not UNSET:
            field_dict["daemon_id"] = daemon_id
        if daemon_status is not UNSET:
            field_dict["daemon_status"] = daemon_status
        if model_catalog is not UNSET:
            field_dict["model_catalog"] = model_catalog
        if models is not UNSET:
            field_dict["models"] = models

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.runtime_model_catalog_entry import RuntimeModelCatalogEntry

        d = dict(src_dict)
        display_name = d.pop("display_name")

        harness_kind = HarnessKind(d.pop("harness_kind"))

        def _parse_availability_status(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        availability_status = _parse_availability_status(
            d.pop("availability_status", UNSET)
        )

        available = d.pop("available", UNSET)

        def _parse_daemon_display_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        daemon_display_name = _parse_daemon_display_name(
            d.pop("daemon_display_name", UNSET)
        )

        def _parse_daemon_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                daemon_id_type_0 = UUID(data)

                return daemon_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | UUID, data)

        daemon_id = _parse_daemon_id(d.pop("daemon_id", UNSET))

        def _parse_daemon_status(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        daemon_status = _parse_daemon_status(d.pop("daemon_status", UNSET))

        _model_catalog = d.pop("model_catalog", UNSET)
        model_catalog: list[RuntimeModelCatalogEntry] | Unset = UNSET
        if _model_catalog is not UNSET:
            model_catalog = []
            for model_catalog_item_data in _model_catalog:
                model_catalog_item = RuntimeModelCatalogEntry.from_dict(
                    model_catalog_item_data
                )

                model_catalog.append(model_catalog_item)

        models = cast(list[str], d.pop("models", UNSET))

        agent_harness_info = cls(
            display_name=display_name,
            harness_kind=harness_kind,
            availability_status=availability_status,
            available=available,
            daemon_display_name=daemon_display_name,
            daemon_id=daemon_id,
            daemon_status=daemon_status,
            model_catalog=model_catalog,
            models=models,
        )

        agent_harness_info.additional_properties = d
        return agent_harness_info

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
