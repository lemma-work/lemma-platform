from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.rung import Rung
from ..models.rung_outcome import RungOutcome
from ..types import UNSET, Unset

T = TypeVar("T", bound="RungTrace")


@_attrs_define
class RungTrace:
    """What one rung did for one decision, for the record and for debugging.

    Attributes:
        outcome (RungOutcome):
        questions (list[str]):
        rung (Rung): Who answered. The first three are the ladder; the last two resolve it.
        input_tokens (int | None | Unset):
        latency_ms (int | Unset):  Default: 0.
        model (None | str | Unset):
    """

    outcome: RungOutcome
    questions: list[str]
    rung: Rung
    input_tokens: int | None | Unset = UNSET
    latency_ms: int | Unset = 0
    model: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        outcome = self.outcome.value

        questions = self.questions

        rung = self.rung.value

        input_tokens: int | None | Unset
        if isinstance(self.input_tokens, Unset):
            input_tokens = UNSET
        else:
            input_tokens = self.input_tokens

        latency_ms = self.latency_ms

        model: None | str | Unset
        if isinstance(self.model, Unset):
            model = UNSET
        else:
            model = self.model

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "outcome": outcome,
                "questions": questions,
                "rung": rung,
            }
        )
        if input_tokens is not UNSET:
            field_dict["input_tokens"] = input_tokens
        if latency_ms is not UNSET:
            field_dict["latency_ms"] = latency_ms
        if model is not UNSET:
            field_dict["model"] = model

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        outcome = RungOutcome(d.pop("outcome"))

        questions = cast(list[str], d.pop("questions"))

        rung = Rung(d.pop("rung"))

        def _parse_input_tokens(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        input_tokens = _parse_input_tokens(d.pop("input_tokens", UNSET))

        latency_ms = d.pop("latency_ms", UNSET)

        def _parse_model(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        model = _parse_model(d.pop("model", UNSET))

        rung_trace = cls(
            outcome=outcome,
            questions=questions,
            rung=rung,
            input_tokens=input_tokens,
            latency_ms=latency_ms,
            model=model,
        )

        rung_trace.additional_properties = d
        return rung_trace

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
