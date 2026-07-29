from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.runtime_profile_status import RuntimeProfileStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.update_runtime_profile_request_config_selections_type_0 import (
        UpdateRuntimeProfileRequestConfigSelectionsType0,
    )
    from ..models.update_runtime_profile_request_headers_type_0 import (
        UpdateRuntimeProfileRequestHeadersType0,
    )
    from ..models.update_runtime_profile_request_model_settings_type_0 import (
        UpdateRuntimeProfileRequestModelSettingsType0,
    )
    from ..models.update_runtime_profile_request_service_account_json_type_0 import (
        UpdateRuntimeProfileRequestServiceAccountJsonType0,
    )


T = TypeVar("T", bound="UpdateRuntimeProfileRequest")


@_attrs_define
class UpdateRuntimeProfileRequest:
    """
    Attributes:
        api_key (None | str | Unset):
        api_version (None | str | Unset):
        azure_endpoint (None | str | Unset):
        base_url (None | str | Unset):
        config_selections (None | Unset | UpdateRuntimeProfileRequestConfigSelectionsType0):
        default_model_name (None | str | Unset):
        description (None | str | Unset):
        fallback_profile_id (None | str | Unset):
        harness_snapshot_revision (None | str | Unset):
        headers (None | Unset | UpdateRuntimeProfileRequestHeadersType0):
        host_wait_timeout_seconds (int | None | Unset):
        location (None | str | Unset):
        model_names (list[str] | None | Unset):
        model_settings (None | Unset | UpdateRuntimeProfileRequestModelSettingsType0):
        name (None | str | Unset):
        project_id (None | str | Unset):
        service_account_json (None | Unset | UpdateRuntimeProfileRequestServiceAccountJsonType0):
        status (None | RuntimeProfileStatus | Unset):
    """

    api_key: None | str | Unset = UNSET
    api_version: None | str | Unset = UNSET
    azure_endpoint: None | str | Unset = UNSET
    base_url: None | str | Unset = UNSET
    config_selections: (
        None | Unset | UpdateRuntimeProfileRequestConfigSelectionsType0
    ) = UNSET
    default_model_name: None | str | Unset = UNSET
    description: None | str | Unset = UNSET
    fallback_profile_id: None | str | Unset = UNSET
    harness_snapshot_revision: None | str | Unset = UNSET
    headers: None | Unset | UpdateRuntimeProfileRequestHeadersType0 = UNSET
    host_wait_timeout_seconds: int | None | Unset = UNSET
    location: None | str | Unset = UNSET
    model_names: list[str] | None | Unset = UNSET
    model_settings: None | Unset | UpdateRuntimeProfileRequestModelSettingsType0 = UNSET
    name: None | str | Unset = UNSET
    project_id: None | str | Unset = UNSET
    service_account_json: (
        None | Unset | UpdateRuntimeProfileRequestServiceAccountJsonType0
    ) = UNSET
    status: None | RuntimeProfileStatus | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.update_runtime_profile_request_config_selections_type_0 import (
            UpdateRuntimeProfileRequestConfigSelectionsType0,
        )
        from ..models.update_runtime_profile_request_headers_type_0 import (
            UpdateRuntimeProfileRequestHeadersType0,
        )
        from ..models.update_runtime_profile_request_model_settings_type_0 import (
            UpdateRuntimeProfileRequestModelSettingsType0,
        )
        from ..models.update_runtime_profile_request_service_account_json_type_0 import (
            UpdateRuntimeProfileRequestServiceAccountJsonType0,
        )

        api_key: None | str | Unset
        if isinstance(self.api_key, Unset):
            api_key = UNSET
        else:
            api_key = self.api_key

        api_version: None | str | Unset
        if isinstance(self.api_version, Unset):
            api_version = UNSET
        else:
            api_version = self.api_version

        azure_endpoint: None | str | Unset
        if isinstance(self.azure_endpoint, Unset):
            azure_endpoint = UNSET
        else:
            azure_endpoint = self.azure_endpoint

        base_url: None | str | Unset
        if isinstance(self.base_url, Unset):
            base_url = UNSET
        else:
            base_url = self.base_url

        config_selections: dict[str, Any] | None | Unset
        if isinstance(self.config_selections, Unset):
            config_selections = UNSET
        elif isinstance(
            self.config_selections, UpdateRuntimeProfileRequestConfigSelectionsType0
        ):
            config_selections = self.config_selections.to_dict()
        else:
            config_selections = self.config_selections

        default_model_name: None | str | Unset
        if isinstance(self.default_model_name, Unset):
            default_model_name = UNSET
        else:
            default_model_name = self.default_model_name

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        fallback_profile_id: None | str | Unset
        if isinstance(self.fallback_profile_id, Unset):
            fallback_profile_id = UNSET
        else:
            fallback_profile_id = self.fallback_profile_id

        harness_snapshot_revision: None | str | Unset
        if isinstance(self.harness_snapshot_revision, Unset):
            harness_snapshot_revision = UNSET
        else:
            harness_snapshot_revision = self.harness_snapshot_revision

        headers: dict[str, Any] | None | Unset
        if isinstance(self.headers, Unset):
            headers = UNSET
        elif isinstance(self.headers, UpdateRuntimeProfileRequestHeadersType0):
            headers = self.headers.to_dict()
        else:
            headers = self.headers

        host_wait_timeout_seconds: int | None | Unset
        if isinstance(self.host_wait_timeout_seconds, Unset):
            host_wait_timeout_seconds = UNSET
        else:
            host_wait_timeout_seconds = self.host_wait_timeout_seconds

        location: None | str | Unset
        if isinstance(self.location, Unset):
            location = UNSET
        else:
            location = self.location

        model_names: list[str] | None | Unset
        if isinstance(self.model_names, Unset):
            model_names = UNSET
        elif isinstance(self.model_names, list):
            model_names = self.model_names

        else:
            model_names = self.model_names

        model_settings: dict[str, Any] | None | Unset
        if isinstance(self.model_settings, Unset):
            model_settings = UNSET
        elif isinstance(
            self.model_settings, UpdateRuntimeProfileRequestModelSettingsType0
        ):
            model_settings = self.model_settings.to_dict()
        else:
            model_settings = self.model_settings

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        project_id: None | str | Unset
        if isinstance(self.project_id, Unset):
            project_id = UNSET
        else:
            project_id = self.project_id

        service_account_json: dict[str, Any] | None | Unset
        if isinstance(self.service_account_json, Unset):
            service_account_json = UNSET
        elif isinstance(
            self.service_account_json,
            UpdateRuntimeProfileRequestServiceAccountJsonType0,
        ):
            service_account_json = self.service_account_json.to_dict()
        else:
            service_account_json = self.service_account_json

        status: None | str | Unset
        if isinstance(self.status, Unset):
            status = UNSET
        elif isinstance(self.status, RuntimeProfileStatus):
            status = self.status.value
        else:
            status = self.status

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if api_key is not UNSET:
            field_dict["api_key"] = api_key
        if api_version is not UNSET:
            field_dict["api_version"] = api_version
        if azure_endpoint is not UNSET:
            field_dict["azure_endpoint"] = azure_endpoint
        if base_url is not UNSET:
            field_dict["base_url"] = base_url
        if config_selections is not UNSET:
            field_dict["config_selections"] = config_selections
        if default_model_name is not UNSET:
            field_dict["default_model_name"] = default_model_name
        if description is not UNSET:
            field_dict["description"] = description
        if fallback_profile_id is not UNSET:
            field_dict["fallback_profile_id"] = fallback_profile_id
        if harness_snapshot_revision is not UNSET:
            field_dict["harness_snapshot_revision"] = harness_snapshot_revision
        if headers is not UNSET:
            field_dict["headers"] = headers
        if host_wait_timeout_seconds is not UNSET:
            field_dict["host_wait_timeout_seconds"] = host_wait_timeout_seconds
        if location is not UNSET:
            field_dict["location"] = location
        if model_names is not UNSET:
            field_dict["model_names"] = model_names
        if model_settings is not UNSET:
            field_dict["model_settings"] = model_settings
        if name is not UNSET:
            field_dict["name"] = name
        if project_id is not UNSET:
            field_dict["project_id"] = project_id
        if service_account_json is not UNSET:
            field_dict["service_account_json"] = service_account_json
        if status is not UNSET:
            field_dict["status"] = status

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.update_runtime_profile_request_config_selections_type_0 import (
            UpdateRuntimeProfileRequestConfigSelectionsType0,
        )
        from ..models.update_runtime_profile_request_headers_type_0 import (
            UpdateRuntimeProfileRequestHeadersType0,
        )
        from ..models.update_runtime_profile_request_model_settings_type_0 import (
            UpdateRuntimeProfileRequestModelSettingsType0,
        )
        from ..models.update_runtime_profile_request_service_account_json_type_0 import (
            UpdateRuntimeProfileRequestServiceAccountJsonType0,
        )

        d = dict(src_dict)

        def _parse_api_key(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        api_key = _parse_api_key(d.pop("api_key", UNSET))

        def _parse_api_version(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        api_version = _parse_api_version(d.pop("api_version", UNSET))

        def _parse_azure_endpoint(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        azure_endpoint = _parse_azure_endpoint(d.pop("azure_endpoint", UNSET))

        def _parse_base_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        base_url = _parse_base_url(d.pop("base_url", UNSET))

        def _parse_config_selections(
            data: object,
        ) -> None | Unset | UpdateRuntimeProfileRequestConfigSelectionsType0:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_selections_type_0 = (
                    UpdateRuntimeProfileRequestConfigSelectionsType0.from_dict(data)
                )

                return config_selections_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(
                None | Unset | UpdateRuntimeProfileRequestConfigSelectionsType0, data
            )

        config_selections = _parse_config_selections(d.pop("config_selections", UNSET))

        def _parse_default_model_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        default_model_name = _parse_default_model_name(
            d.pop("default_model_name", UNSET)
        )

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_fallback_profile_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        fallback_profile_id = _parse_fallback_profile_id(
            d.pop("fallback_profile_id", UNSET)
        )

        def _parse_harness_snapshot_revision(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        harness_snapshot_revision = _parse_harness_snapshot_revision(
            d.pop("harness_snapshot_revision", UNSET)
        )

        def _parse_headers(
            data: object,
        ) -> None | Unset | UpdateRuntimeProfileRequestHeadersType0:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                headers_type_0 = UpdateRuntimeProfileRequestHeadersType0.from_dict(data)

                return headers_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UpdateRuntimeProfileRequestHeadersType0, data)

        headers = _parse_headers(d.pop("headers", UNSET))

        def _parse_host_wait_timeout_seconds(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        host_wait_timeout_seconds = _parse_host_wait_timeout_seconds(
            d.pop("host_wait_timeout_seconds", UNSET)
        )

        def _parse_location(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        location = _parse_location(d.pop("location", UNSET))

        def _parse_model_names(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                model_names_type_0 = cast(list[str], data)

                return model_names_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[str] | None | Unset, data)

        model_names = _parse_model_names(d.pop("model_names", UNSET))

        def _parse_model_settings(
            data: object,
        ) -> None | Unset | UpdateRuntimeProfileRequestModelSettingsType0:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                model_settings_type_0 = (
                    UpdateRuntimeProfileRequestModelSettingsType0.from_dict(data)
                )

                return model_settings_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(
                None | Unset | UpdateRuntimeProfileRequestModelSettingsType0, data
            )

        model_settings = _parse_model_settings(d.pop("model_settings", UNSET))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_project_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        project_id = _parse_project_id(d.pop("project_id", UNSET))

        def _parse_service_account_json(
            data: object,
        ) -> None | Unset | UpdateRuntimeProfileRequestServiceAccountJsonType0:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                service_account_json_type_0 = (
                    UpdateRuntimeProfileRequestServiceAccountJsonType0.from_dict(data)
                )

                return service_account_json_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(
                None | Unset | UpdateRuntimeProfileRequestServiceAccountJsonType0, data
            )

        service_account_json = _parse_service_account_json(
            d.pop("service_account_json", UNSET)
        )

        def _parse_status(data: object) -> None | RuntimeProfileStatus | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                status_type_0 = RuntimeProfileStatus(data)

                return status_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | RuntimeProfileStatus | Unset, data)

        status = _parse_status(d.pop("status", UNSET))

        update_runtime_profile_request = cls(
            api_key=api_key,
            api_version=api_version,
            azure_endpoint=azure_endpoint,
            base_url=base_url,
            config_selections=config_selections,
            default_model_name=default_model_name,
            description=description,
            fallback_profile_id=fallback_profile_id,
            harness_snapshot_revision=harness_snapshot_revision,
            headers=headers,
            host_wait_timeout_seconds=host_wait_timeout_seconds,
            location=location,
            model_names=model_names,
            model_settings=model_settings,
            name=name,
            project_id=project_id,
            service_account_json=service_account_json,
            status=status,
        )

        return update_runtime_profile_request
