from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.process_response_state import ProcessResponseState
from ..types import UNSET, Unset

T = TypeVar("T", bound="ProcessResponse")


@_attrs_define
class ProcessResponse:
    """
    Attributes:
        command (str):
        cwd (str):
        id (str):
        state (ProcessResponseState):
        exit_code (int | None | Unset):
        run_id (None | str | Unset):
        service (None | str | Unset):
        started_at (None | str | Unset):
        started_by (None | Unset | UUID):
        tty (bool | Unset):  Default: False.
    """

    command: str
    cwd: str
    id: str
    state: ProcessResponseState
    exit_code: int | None | Unset = UNSET
    run_id: None | str | Unset = UNSET
    service: None | str | Unset = UNSET
    started_at: None | str | Unset = UNSET
    started_by: None | Unset | UUID = UNSET
    tty: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        command = self.command

        cwd = self.cwd

        id = self.id

        state = self.state.value

        exit_code: int | None | Unset
        if isinstance(self.exit_code, Unset):
            exit_code = UNSET
        else:
            exit_code = self.exit_code

        run_id: None | str | Unset
        if isinstance(self.run_id, Unset):
            run_id = UNSET
        else:
            run_id = self.run_id

        service: None | str | Unset
        if isinstance(self.service, Unset):
            service = UNSET
        else:
            service = self.service

        started_at: None | str | Unset
        if isinstance(self.started_at, Unset):
            started_at = UNSET
        else:
            started_at = self.started_at

        started_by: None | str | Unset
        if isinstance(self.started_by, Unset):
            started_by = UNSET
        elif isinstance(self.started_by, UUID):
            started_by = str(self.started_by)
        else:
            started_by = self.started_by

        tty = self.tty

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "command": command,
                "cwd": cwd,
                "id": id,
                "state": state,
            }
        )
        if exit_code is not UNSET:
            field_dict["exit_code"] = exit_code
        if run_id is not UNSET:
            field_dict["run_id"] = run_id
        if service is not UNSET:
            field_dict["service"] = service
        if started_at is not UNSET:
            field_dict["started_at"] = started_at
        if started_by is not UNSET:
            field_dict["started_by"] = started_by
        if tty is not UNSET:
            field_dict["tty"] = tty

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        command = d.pop("command")

        cwd = d.pop("cwd")

        id = d.pop("id")

        state = ProcessResponseState(d.pop("state"))

        def _parse_exit_code(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        exit_code = _parse_exit_code(d.pop("exit_code", UNSET))

        def _parse_run_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        run_id = _parse_run_id(d.pop("run_id", UNSET))

        def _parse_service(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        service = _parse_service(d.pop("service", UNSET))

        def _parse_started_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        started_at = _parse_started_at(d.pop("started_at", UNSET))

        def _parse_started_by(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                started_by_type_0 = UUID(data)

                return started_by_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        started_by = _parse_started_by(d.pop("started_by", UNSET))

        tty = d.pop("tty", UNSET)

        process_response = cls(
            command=command,
            cwd=cwd,
            id=id,
            state=state,
            exit_code=exit_code,
            run_id=run_id,
            service=service,
            started_at=started_at,
            started_by=started_by,
            tty=tty,
        )

        process_response.additional_properties = d
        return process_response

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
