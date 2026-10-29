from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.started_pod_response import StartedPodResponse


T = TypeVar("T", bound="StartLemmaResponse")


@_attrs_define
class StartLemmaResponse:
    """
    Attributes:
        conversation_id (UUID): The chat where its agent sets itself up, opened with the person's first message. From a
            template, its first turn starts once the template is applied.
        pod (StartedPodResponse):
        import_id (None | Unset | UUID): The template's import, readable at GET
            /pods/{pod_id}/bundle/imports/{import_id} while it is applied; null without a template.
    """

    conversation_id: UUID
    pod: StartedPodResponse
    import_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        conversation_id = str(self.conversation_id)

        pod = self.pod.to_dict()

        import_id: None | str | Unset
        if isinstance(self.import_id, Unset):
            import_id = UNSET
        elif isinstance(self.import_id, UUID):
            import_id = str(self.import_id)
        else:
            import_id = self.import_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "conversation_id": conversation_id,
                "pod": pod,
            }
        )
        if import_id is not UNSET:
            field_dict["import_id"] = import_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.started_pod_response import StartedPodResponse

        d = dict(src_dict)
        conversation_id = UUID(d.pop("conversation_id"))

        pod = StartedPodResponse.from_dict(d.pop("pod"))

        def _parse_import_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                import_id_type_0 = UUID(data)

                return import_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        import_id = _parse_import_id(d.pop("import_id", UNSET))

        start_lemma_response = cls(
            conversation_id=conversation_id,
            pod=pod,
            import_id=import_id,
        )

        start_lemma_response.additional_properties = d
        return start_lemma_response

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
