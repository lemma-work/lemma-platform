from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.output_chunk_model_channel import OutputChunkModelChannel

T = TypeVar("T", bound="OutputChunkModel")


@_attrs_define
class OutputChunkModel:
    """
    Attributes:
        channel (OutputChunkModelChannel):
        data (str): UTF-8, with undecodable bytes replaced.
        sequence (int):
    """

    channel: OutputChunkModelChannel
    data: str
    sequence: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        channel = self.channel.value

        data = self.data

        sequence = self.sequence

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "channel": channel,
                "data": data,
                "sequence": sequence,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        channel = OutputChunkModelChannel(d.pop("channel"))

        data = d.pop("data")

        sequence = d.pop("sequence")

        output_chunk_model = cls(
            channel=channel,
            data=data,
            sequence=sequence,
        )

        output_chunk_model.additional_properties = d
        return output_chunk_model

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
