"""Modal pickers for server / org / pod switching.

Pure UI: the caller supplies already-loaded items and receives the chosen
item's id (or None on dismiss) via the screen result.
"""

from __future__ import annotations

from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


def _label(item: dict[str, Any]) -> str:
    name = str(item.get("name") or item.get("title") or item.get("id") or "")
    subtitle = str(item.get("slug") or item.get("base_url") or item.get("id") or "")
    marker = "● " if item.get("active") else "  "
    if subtitle and subtitle != name:
        return f"{marker}{name}  ({subtitle})"
    return f"{marker}{name}"


class PickerScreen(ModalScreen[str | None]):
    """Filterable list picker; resolves to the selected item id."""

    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, *, title: str, items: list[dict[str, Any]]) -> None:
        super().__init__()
        self._title = title
        self._items = items

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Static(self._title, id="picker-title")
            yield Input(placeholder="filter…", id="picker-filter")
            yield OptionList(id="picker-options")

    def on_mount(self) -> None:
        self._populate(self._items)
        self.query_one("#picker-options", OptionList).focus()

    def _populate(self, items: list[dict[str, Any]]) -> None:
        options = self.query_one("#picker-options", OptionList)
        options.clear_options()
        for item in items:
            options.add_option(Option(_label(item), id=str(item.get("id"))))

    @on(Input.Changed, "#picker-filter")
    def _filter(self, event: Input.Changed) -> None:
        needle = event.value.strip().lower()
        filtered = [
            item
            for item in self._items
            if needle in _label(item).lower()
            or needle in str(item.get("id", "")).lower()
        ]
        self._populate(filtered if needle else self._items)

    @on(Input.Submitted, "#picker-filter")
    def _focus_options(self) -> None:
        self.query_one("#picker-options", OptionList).focus()

    @on(OptionList.OptionSelected, "#picker-options")
    def _selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)


class ServerPickerScreen(PickerScreen):
    def __init__(self, items: list[dict[str, Any]]) -> None:
        super().__init__(title="Switch server", items=items)


class OrgPickerScreen(PickerScreen):
    def __init__(self, items: list[dict[str, Any]]) -> None:
        super().__init__(title="Select organization", items=items)


class PodPickerScreen(PickerScreen):
    def __init__(self, items: list[dict[str, Any]]) -> None:
        super().__init__(title="Select pod", items=items)
