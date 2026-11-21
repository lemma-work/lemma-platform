from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.plugin_install_state import PluginInstallState
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.export_progress_response import ExportProgressResponse
    from ..models.plugin_install_step_response import PluginInstallStepResponse


T = TypeVar("T", bound="PluginInstallItemResponse")


@_attrs_define
class PluginInstallItemResponse:
    """Where one plugin of an install is.

    Attributes:
        import_id (UUID): Its import: GET /pods/{pod_id}/bundle/imports/{import_id} has its whole plan and every step.
        name (str): The bundle's own name once planned, else its folder's.
        position (int): Its place in the request, from 0.
        repo_url (str):
        state (PluginInstallState): Where one bundle of an install is, as the person would say it.
        app (None | str | Unset): The app it put in the Lemma, once INSTALLED, if it brings one.
        connect_url (None | str | Unset): Where to connect the account a failure names, if it names one.
        current_step (None | PluginInstallStepResponse | Unset): The step running now, if one is.
        error_code (None | str | Unset):
        path (None | str | Unset):
        progress (ExportProgressResponse | Unset):
        reason (None | str | Unset): Why it stopped: the question its page asks (NEEDS_ANSWER), or what failed (FAILED).
        ref (None | str | Unset):
        warnings (list[str] | Unset):
    """

    import_id: UUID
    name: str
    position: int
    repo_url: str
    state: PluginInstallState
    app: None | str | Unset = UNSET
    connect_url: None | str | Unset = UNSET
    current_step: None | PluginInstallStepResponse | Unset = UNSET
    error_code: None | str | Unset = UNSET
    path: None | str | Unset = UNSET
    progress: ExportProgressResponse | Unset = UNSET
    reason: None | str | Unset = UNSET
    ref: None | str | Unset = UNSET
    warnings: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.plugin_install_step_response import PluginInstallStepResponse

        import_id = str(self.import_id)

        name = self.name

        position = self.position

        repo_url = self.repo_url

        state = self.state.value

        app: None | str | Unset
        if isinstance(self.app, Unset):
            app = UNSET
        else:
            app = self.app

        connect_url: None | str | Unset
        if isinstance(self.connect_url, Unset):
            connect_url = UNSET
        else:
            connect_url = self.connect_url

        current_step: dict[str, Any] | None | Unset
        if isinstance(self.current_step, Unset):
            current_step = UNSET
        elif isinstance(self.current_step, PluginInstallStepResponse):
            current_step = self.current_step.to_dict()
        else:
            current_step = self.current_step

        error_code: None | str | Unset
        if isinstance(self.error_code, Unset):
            error_code = UNSET
        else:
            error_code = self.error_code

        path: None | str | Unset
        if isinstance(self.path, Unset):
            path = UNSET
        else:
            path = self.path

        progress: dict[str, Any] | Unset = UNSET
        if not isinstance(self.progress, Unset):
            progress = self.progress.to_dict()

        reason: None | str | Unset
        if isinstance(self.reason, Unset):
            reason = UNSET
        else:
            reason = self.reason

        ref: None | str | Unset
        if isinstance(self.ref, Unset):
            ref = UNSET
        else:
            ref = self.ref

        warnings: list[str] | Unset = UNSET
        if not isinstance(self.warnings, Unset):
            warnings = self.warnings

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "import_id": import_id,
                "name": name,
                "position": position,
                "repo_url": repo_url,
                "state": state,
            }
        )
        if app is not UNSET:
            field_dict["app"] = app
        if connect_url is not UNSET:
            field_dict["connect_url"] = connect_url
        if current_step is not UNSET:
            field_dict["current_step"] = current_step
        if error_code is not UNSET:
            field_dict["error_code"] = error_code
        if path is not UNSET:
            field_dict["path"] = path
        if progress is not UNSET:
            field_dict["progress"] = progress
        if reason is not UNSET:
            field_dict["reason"] = reason
        if ref is not UNSET:
            field_dict["ref"] = ref
        if warnings is not UNSET:
            field_dict["warnings"] = warnings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.export_progress_response import ExportProgressResponse
        from ..models.plugin_install_step_response import PluginInstallStepResponse

        d = dict(src_dict)
        import_id = UUID(d.pop("import_id"))

        name = d.pop("name")

        position = d.pop("position")

        repo_url = d.pop("repo_url")

        state = PluginInstallState(d.pop("state"))

        def _parse_app(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        app = _parse_app(d.pop("app", UNSET))

        def _parse_connect_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        connect_url = _parse_connect_url(d.pop("connect_url", UNSET))

        def _parse_current_step(
            data: object,
        ) -> None | PluginInstallStepResponse | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                current_step_type_0 = PluginInstallStepResponse.from_dict(data)

                return current_step_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PluginInstallStepResponse | Unset, data)

        current_step = _parse_current_step(d.pop("current_step", UNSET))

        def _parse_error_code(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error_code = _parse_error_code(d.pop("error_code", UNSET))

        def _parse_path(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        path = _parse_path(d.pop("path", UNSET))

        _progress = d.pop("progress", UNSET)
        progress: ExportProgressResponse | Unset
        if isinstance(_progress, Unset):
            progress = UNSET
        else:
            progress = ExportProgressResponse.from_dict(_progress)

        def _parse_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        reason = _parse_reason(d.pop("reason", UNSET))

        def _parse_ref(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        ref = _parse_ref(d.pop("ref", UNSET))

        warnings = cast(list[str], d.pop("warnings", UNSET))

        plugin_install_item_response = cls(
            import_id=import_id,
            name=name,
            position=position,
            repo_url=repo_url,
            state=state,
            app=app,
            connect_url=connect_url,
            current_step=current_step,
            error_code=error_code,
            path=path,
            progress=progress,
            reason=reason,
            ref=ref,
            warnings=warnings,
        )

        plugin_install_item_response.additional_properties = d
        return plugin_install_item_response

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
