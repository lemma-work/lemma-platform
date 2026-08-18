from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="DirectoryTreeNode")


@_attrs_define
class DirectoryTreeNode:
    """
    Attributes:
        kind (str):
        name (str):
        path (str):
        children (list[DirectoryTreeNode] | Unset):
        has_markdown (bool | None | Unset):
        has_more_files (bool | Unset):  Default: False.
        indexed (bool | None | Unset):
        status (None | str | Unset):
        visibility (None | str | Unset):
    """

    kind: str
    name: str
    path: str
    children: list[DirectoryTreeNode] | Unset = UNSET
    has_markdown: bool | None | Unset = UNSET
    has_more_files: bool | Unset = False
    indexed: bool | None | Unset = UNSET
    status: None | str | Unset = UNSET
    visibility: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind

        name = self.name

        path = self.path

        children: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.children, Unset):
            children = []
            for children_item_data in self.children:
                children_item = children_item_data.to_dict()
                children.append(children_item)

        has_markdown: bool | None | Unset
        if isinstance(self.has_markdown, Unset):
            has_markdown = UNSET
        else:
            has_markdown = self.has_markdown

        has_more_files = self.has_more_files

        indexed: bool | None | Unset
        if isinstance(self.indexed, Unset):
            indexed = UNSET
        else:
            indexed = self.indexed

        status: None | str | Unset
        if isinstance(self.status, Unset):
            status = UNSET
        else:
            status = self.status

        visibility: None | str | Unset
        if isinstance(self.visibility, Unset):
            visibility = UNSET
        else:
            visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "kind": kind,
                "name": name,
                "path": path,
            }
        )
        if children is not UNSET:
            field_dict["children"] = children
        if has_markdown is not UNSET:
            field_dict["has_markdown"] = has_markdown
        if has_more_files is not UNSET:
            field_dict["has_more_files"] = has_more_files
        if indexed is not UNSET:
            field_dict["indexed"] = indexed
        if status is not UNSET:
            field_dict["status"] = status
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        kind = d.pop("kind")

        name = d.pop("name")

        path = d.pop("path")

        _children = d.pop("children", UNSET)
        children: list[DirectoryTreeNode] | Unset = UNSET
        if _children is not UNSET:
            children = []
            for children_item_data in _children:
                children_item = DirectoryTreeNode.from_dict(children_item_data)

                children.append(children_item)

        def _parse_has_markdown(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        has_markdown = _parse_has_markdown(d.pop("has_markdown", UNSET))

        has_more_files = d.pop("has_more_files", UNSET)

        def _parse_indexed(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        indexed = _parse_indexed(d.pop("indexed", UNSET))

        def _parse_status(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        status = _parse_status(d.pop("status", UNSET))

        def _parse_visibility(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        visibility = _parse_visibility(d.pop("visibility", UNSET))

        directory_tree_node = cls(
            kind=kind,
            name=name,
            path=path,
            children=children,
            has_markdown=has_markdown,
            has_more_files=has_more_files,
            indexed=indexed,
            status=status,
            visibility=visibility,
        )

        directory_tree_node.additional_properties = d
        return directory_tree_node

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
