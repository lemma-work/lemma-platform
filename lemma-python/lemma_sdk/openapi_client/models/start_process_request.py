from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.start_process_request_env import StartProcessRequestEnv
    from ..models.terminal_size_model import TerminalSizeModel


T = TypeVar("T", bound="StartProcessRequest")


@_attrs_define
class StartProcessRequest:
    """
    Attributes:
        command (str):
        cwd (None | str | Unset): Inside the machine's home: absolute, `~/...`, or relative to ~/lemma. Defaults to
            ~/lemma.
        env (StartProcessRequestEnv | Unset):
        restart_on_resume (bool | Unset): With `service`: start it again after a pause. Default: False.
        run_id (None | str | Unset): Stop it when this run ends (pod machines tag agent work this way).
        service (None | str | Unset): Run as a named service: no lifetime cap, not stopped when the run that started it
            ends, listed under Processes as a service.
        timeout_seconds (int | None | Unset): How long it may run. Capped at the machine's process lifetime.
        tty (None | TerminalSizeModel | Unset): Run in a terminal of this size (for a shell).
    """

    command: str
    cwd: None | str | Unset = UNSET
    env: StartProcessRequestEnv | Unset = UNSET
    restart_on_resume: bool | Unset = False
    run_id: None | str | Unset = UNSET
    service: None | str | Unset = UNSET
    timeout_seconds: int | None | Unset = UNSET
    tty: None | TerminalSizeModel | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.terminal_size_model import TerminalSizeModel

        command = self.command

        cwd: None | str | Unset
        if isinstance(self.cwd, Unset):
            cwd = UNSET
        else:
            cwd = self.cwd

        env: dict[str, Any] | Unset = UNSET
        if not isinstance(self.env, Unset):
            env = self.env.to_dict()

        restart_on_resume = self.restart_on_resume

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

        timeout_seconds: int | None | Unset
        if isinstance(self.timeout_seconds, Unset):
            timeout_seconds = UNSET
        else:
            timeout_seconds = self.timeout_seconds

        tty: dict[str, Any] | None | Unset
        if isinstance(self.tty, Unset):
            tty = UNSET
        elif isinstance(self.tty, TerminalSizeModel):
            tty = self.tty.to_dict()
        else:
            tty = self.tty

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "command": command,
            }
        )
        if cwd is not UNSET:
            field_dict["cwd"] = cwd
        if env is not UNSET:
            field_dict["env"] = env
        if restart_on_resume is not UNSET:
            field_dict["restart_on_resume"] = restart_on_resume
        if run_id is not UNSET:
            field_dict["run_id"] = run_id
        if service is not UNSET:
            field_dict["service"] = service
        if timeout_seconds is not UNSET:
            field_dict["timeout_seconds"] = timeout_seconds
        if tty is not UNSET:
            field_dict["tty"] = tty

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.start_process_request_env import StartProcessRequestEnv
        from ..models.terminal_size_model import TerminalSizeModel

        d = dict(src_dict)
        command = d.pop("command")

        def _parse_cwd(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        cwd = _parse_cwd(d.pop("cwd", UNSET))

        _env = d.pop("env", UNSET)
        env: StartProcessRequestEnv | Unset
        if isinstance(_env, Unset):
            env = UNSET
        else:
            env = StartProcessRequestEnv.from_dict(_env)

        restart_on_resume = d.pop("restart_on_resume", UNSET)

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

        def _parse_timeout_seconds(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        timeout_seconds = _parse_timeout_seconds(d.pop("timeout_seconds", UNSET))

        def _parse_tty(data: object) -> None | TerminalSizeModel | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                tty_type_0 = TerminalSizeModel.from_dict(data)

                return tty_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | TerminalSizeModel | Unset, data)

        tty = _parse_tty(d.pop("tty", UNSET))

        start_process_request = cls(
            command=command,
            cwd=cwd,
            env=env,
            restart_on_resume=restart_on_resume,
            run_id=run_id,
            service=service,
            timeout_seconds=timeout_seconds,
            tty=tty,
        )

        start_process_request.additional_properties = d
        return start_process_request

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
