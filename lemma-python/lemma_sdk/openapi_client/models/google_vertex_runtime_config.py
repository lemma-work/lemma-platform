from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.google_vertex_runtime_config_model_settings import (
        GoogleVertexRuntimeConfigModelSettings,
    )


T = TypeVar("T", bound="GoogleVertexRuntimeConfig")


@_attrs_define
class GoogleVertexRuntimeConfig:
    """
    Attributes:
        location (str):
        project_id (str):
        model_settings (GoogleVertexRuntimeConfigModelSettings | Unset):
    """

    location: str
    project_id: str
    model_settings: GoogleVertexRuntimeConfigModelSettings | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        location = self.location

        project_id = self.project_id

        model_settings: dict[str, Any] | Unset = UNSET
        if not isinstance(self.model_settings, Unset):
            model_settings = self.model_settings.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "location": location,
                "project_id": project_id,
            }
        )
        if model_settings is not UNSET:
            field_dict["model_settings"] = model_settings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.google_vertex_runtime_config_model_settings import (
            GoogleVertexRuntimeConfigModelSettings,
        )

        d = dict(src_dict)
        location = d.pop("location")

        project_id = d.pop("project_id")

        _model_settings = d.pop("model_settings", UNSET)
        model_settings: GoogleVertexRuntimeConfigModelSettings | Unset
        if isinstance(_model_settings, Unset):
            model_settings = UNSET
        else:
            model_settings = GoogleVertexRuntimeConfigModelSettings.from_dict(
                _model_settings
            )

        google_vertex_runtime_config = cls(
            location=location,
            project_id=project_id,
            model_settings=model_settings,
        )

        return google_vertex_runtime_config
