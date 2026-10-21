from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="CreateScheduleRequestConfig")


@_attrs_define
class CreateScheduleRequestConfig:
    """Settings for the schedule's type: `cron`/`scheduled_at`/`timezone` for TIME, `source` for WEBHOOK,
    `table_name`/`operations`/`when` for DATASTORE. Any type may set `max_concurrent_runs`: at most that many of the
    schedule's runs in flight at once, from 1 to 1000; a fire that finds it full is skipped and recorded as skipped, not
    queued. DATASTORE may set `bulk_writes` and `import_writes` to `skip`, `each` or `batch`: what a bulk write or an
    import does to the trigger. Both default to `skip` (the changes are recorded as skipped, aggregated); the pod
    setting `triggers.bulk_writes_default` changes the default for `bulk_writes`.

    """

    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        create_schedule_request_config = cls()

        create_schedule_request_config.additional_properties = d
        return create_schedule_request_config

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
