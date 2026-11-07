from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.output_chunk_model import OutputChunkModel


T = TypeVar("T", bound="ProcessOutputResponse")


@_attrs_define
class ProcessOutputResponse:
    """
    Attributes:
        chunks (list[OutputChunkModel]):
        next_sequence (int):
        state (str):
        exit_code (int | None | Unset):
        truncated_before_sequence (int | None | Unset):
    """

    chunks: list[OutputChunkModel]
    next_sequence: int
    state: str
    exit_code: int | None | Unset = UNSET
    truncated_before_sequence: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        chunks = []
        for chunks_item_data in self.chunks:
            chunks_item = chunks_item_data.to_dict()
            chunks.append(chunks_item)

        next_sequence = self.next_sequence

        state = self.state

        exit_code: int | None | Unset
        if isinstance(self.exit_code, Unset):
            exit_code = UNSET
        else:
            exit_code = self.exit_code

        truncated_before_sequence: int | None | Unset
        if isinstance(self.truncated_before_sequence, Unset):
            truncated_before_sequence = UNSET
        else:
            truncated_before_sequence = self.truncated_before_sequence

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "chunks": chunks,
                "next_sequence": next_sequence,
                "state": state,
            }
        )
        if exit_code is not UNSET:
            field_dict["exit_code"] = exit_code
        if truncated_before_sequence is not UNSET:
            field_dict["truncated_before_sequence"] = truncated_before_sequence

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.output_chunk_model import OutputChunkModel

        d = dict(src_dict)
        chunks = []
        _chunks = d.pop("chunks")
        for chunks_item_data in _chunks:
            chunks_item = OutputChunkModel.from_dict(chunks_item_data)

            chunks.append(chunks_item)

        next_sequence = d.pop("next_sequence")

        state = d.pop("state")

        def _parse_exit_code(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        exit_code = _parse_exit_code(d.pop("exit_code", UNSET))

        def _parse_truncated_before_sequence(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        truncated_before_sequence = _parse_truncated_before_sequence(
            d.pop("truncated_before_sequence", UNSET)
        )

        process_output_response = cls(
            chunks=chunks,
            next_sequence=next_sequence,
            state=state,
            exit_code=exit_code,
            truncated_before_sequence=truncated_before_sequence,
        )

        process_output_response.additional_properties = d
        return process_output_response

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
