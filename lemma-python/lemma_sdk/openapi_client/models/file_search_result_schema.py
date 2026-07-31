from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.file_search_result_schema_metadata_type_0 import (
        FileSearchResultSchemaMetadataType0,
    )


T = TypeVar("T", bound="FileSearchResultSchema")


@_attrs_define
class FileSearchResultSchema:
    """
    Attributes:
        chunk_index (int):
        content (str):
        file_id (UUID):
        path (str):
        score (float):
        metadata (FileSearchResultSchemaMetadataType0 | None | Unset):
        page_end (int | None | Unset):
        page_number (int | None | Unset):
    """

    chunk_index: int
    content: str
    file_id: UUID
    path: str
    score: float
    metadata: FileSearchResultSchemaMetadataType0 | None | Unset = UNSET
    page_end: int | None | Unset = UNSET
    page_number: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.file_search_result_schema_metadata_type_0 import (
            FileSearchResultSchemaMetadataType0,
        )

        chunk_index = self.chunk_index

        content = self.content

        file_id = str(self.file_id)

        path = self.path

        score = self.score

        metadata: dict[str, Any] | None | Unset
        if isinstance(self.metadata, Unset):
            metadata = UNSET
        elif isinstance(self.metadata, FileSearchResultSchemaMetadataType0):
            metadata = self.metadata.to_dict()
        else:
            metadata = self.metadata

        page_end: int | None | Unset
        if isinstance(self.page_end, Unset):
            page_end = UNSET
        else:
            page_end = self.page_end

        page_number: int | None | Unset
        if isinstance(self.page_number, Unset):
            page_number = UNSET
        else:
            page_number = self.page_number

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "chunk_index": chunk_index,
                "content": content,
                "file_id": file_id,
                "path": path,
                "score": score,
            }
        )
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if page_end is not UNSET:
            field_dict["page_end"] = page_end
        if page_number is not UNSET:
            field_dict["page_number"] = page_number

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.file_search_result_schema_metadata_type_0 import (
            FileSearchResultSchemaMetadataType0,
        )

        d = dict(src_dict)
        chunk_index = d.pop("chunk_index")

        content = d.pop("content")

        file_id = UUID(d.pop("file_id"))

        path = d.pop("path")

        score = d.pop("score")

        def _parse_metadata(
            data: object,
        ) -> FileSearchResultSchemaMetadataType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                metadata_type_0 = FileSearchResultSchemaMetadataType0.from_dict(data)

                return metadata_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(FileSearchResultSchemaMetadataType0 | None | Unset, data)

        metadata = _parse_metadata(d.pop("metadata", UNSET))

        def _parse_page_end(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        page_end = _parse_page_end(d.pop("page_end", UNSET))

        def _parse_page_number(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        page_number = _parse_page_number(d.pop("page_number", UNSET))

        file_search_result_schema = cls(
            chunk_index=chunk_index,
            content=content,
            file_id=file_id,
            path=path,
            score=score,
            metadata=metadata,
            page_end=page_end,
            page_number=page_number,
        )

        file_search_result_schema.additional_properties = d
        return file_search_result_schema

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
