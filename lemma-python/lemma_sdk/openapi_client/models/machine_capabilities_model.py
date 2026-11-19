from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="MachineCapabilitiesModel")


@_attrs_define
class MachineCapabilitiesModel:
    """
    Attributes:
        browser (bool):
        docker (bool):
        files (bool):
        previews (bool):
        processes (bool):
        pty (bool):
        python (bool):
    """

    browser: bool
    docker: bool
    files: bool
    previews: bool
    processes: bool
    pty: bool
    python: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        browser = self.browser

        docker = self.docker

        files = self.files

        previews = self.previews

        processes = self.processes

        pty = self.pty

        python = self.python

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "browser": browser,
                "docker": docker,
                "files": files,
                "previews": previews,
                "processes": processes,
                "pty": pty,
                "python": python,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        browser = d.pop("browser")

        docker = d.pop("docker")

        files = d.pop("files")

        previews = d.pop("previews")

        processes = d.pop("processes")

        pty = d.pop("pty")

        python = d.pop("python")

        machine_capabilities_model = cls(
            browser=browser,
            docker=docker,
            files=files,
            previews=previews,
            processes=processes,
            pty=pty,
            python=python,
        )

        machine_capabilities_model.additional_properties = d
        return machine_capabilities_model

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
