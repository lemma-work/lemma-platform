"""Rendering the input view: the only part of a state that leaves this module."""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import JsonValue

from app.modules.decisions.domain.deciders import InputView


@dataclass(frozen=True, slots=True)
class RenderedState:
    #: What rules evaluate against: the selected fields, still structured.
    value: JsonValue
    #: What engines read and what the record keeps as evidence.
    text: str
    truncated: bool


def render(state: JsonValue, view: InputView) -> RenderedState:
    """Select the view's fields from `state`, then bound the text to the view.

    A state that is not an object is kept whole, since there are no fields to
    pick from. A path that does not exist is left out rather than sent as null:
    an engine told `"from": null` reads it as a sender nobody knows.
    """
    selected = _select(state, view.fields) if view.fields else state
    text = selected if isinstance(selected, str) else _compact(selected)
    if len(text) <= view.max_chars:
        return RenderedState(value=selected, text=text, truncated=False)
    dropped = len(text) - view.max_chars
    bounded = (
        f"{text[: view.max_chars]}\n[truncated: {dropped} of {len(text)} characters "
        "left out. Judge from what is shown.]"
    )
    return RenderedState(value=selected, text=bounded, truncated=True)


def _select(state: JsonValue, fields: list[str]) -> JsonValue:
    if not isinstance(state, dict):
        return state
    selected: dict[str, JsonValue] = {}
    for path in fields:
        found, value = _lookup(state, path.split("."))
        if found:
            selected[path] = value
    return selected


def _lookup(value: JsonValue, parts: list[str]) -> tuple[bool, JsonValue]:
    current = value
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _compact(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
