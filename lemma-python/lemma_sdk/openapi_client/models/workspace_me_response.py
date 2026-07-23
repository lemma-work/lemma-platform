from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.workspace_me_response_apps import WorkspaceMeResponseApps
    from ..models.workspace_me_sandbox import WorkspaceMeSandbox
    from ..models.workspace_me_session import WorkspaceMeSession


T = TypeVar("T", bound="WorkspaceMeResponse")


@_attrs_define
class WorkspaceMeResponse:
    """
    Attributes:
        apps (WorkspaceMeResponseApps):
        sandbox (WorkspaceMeSandbox):
        user_id (UUID):
        active_session (None | Unset | WorkspaceMeSession):
    """

    apps: WorkspaceMeResponseApps
    sandbox: WorkspaceMeSandbox
    user_id: UUID
    active_session: None | Unset | WorkspaceMeSession = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.workspace_me_session import WorkspaceMeSession

        apps = self.apps.to_dict()

        sandbox = self.sandbox.to_dict()

        user_id = str(self.user_id)

        active_session: dict[str, Any] | None | Unset
        if isinstance(self.active_session, Unset):
            active_session = UNSET
        elif isinstance(self.active_session, WorkspaceMeSession):
            active_session = self.active_session.to_dict()
        else:
            active_session = self.active_session

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "apps": apps,
                "sandbox": sandbox,
                "user_id": user_id,
            }
        )
        if active_session is not UNSET:
            field_dict["active_session"] = active_session

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.workspace_me_response_apps import WorkspaceMeResponseApps
        from ..models.workspace_me_sandbox import WorkspaceMeSandbox
        from ..models.workspace_me_session import WorkspaceMeSession

        d = dict(src_dict)
        apps = WorkspaceMeResponseApps.from_dict(d.pop("apps"))

        sandbox = WorkspaceMeSandbox.from_dict(d.pop("sandbox"))

        user_id = UUID(d.pop("user_id"))

        def _parse_active_session(data: object) -> None | Unset | WorkspaceMeSession:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                active_session_type_0 = WorkspaceMeSession.from_dict(data)

                return active_session_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | WorkspaceMeSession, data)

        active_session = _parse_active_session(d.pop("active_session", UNSET))

        workspace_me_response = cls(
            apps=apps,
            sandbox=sandbox,
            user_id=user_id,
            active_session=active_session,
        )

        workspace_me_response.additional_properties = d
        return workspace_me_response

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
