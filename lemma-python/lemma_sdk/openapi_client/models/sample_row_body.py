from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.sample_row_body_expected_type_0 import SampleRowBodyExpectedType0


T = TypeVar("T", bound="SampleRowBody")


@_attrs_define
class SampleRowBody:
    """
    Attributes:
        state (Any):
        expected (None | SampleRowBodyExpectedType0 | Unset):
    """

    state: Any
    expected: None | SampleRowBodyExpectedType0 | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.sample_row_body_expected_type_0 import SampleRowBodyExpectedType0

        state = self.state

        expected: dict[str, Any] | None | Unset
        if isinstance(self.expected, Unset):
            expected = UNSET
        elif isinstance(self.expected, SampleRowBodyExpectedType0):
            expected = self.expected.to_dict()
        else:
            expected = self.expected

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "state": state,
            }
        )
        if expected is not UNSET:
            field_dict["expected"] = expected

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.sample_row_body_expected_type_0 import SampleRowBodyExpectedType0

        d = dict(src_dict)
        state = d.pop("state")

        def _parse_expected(data: object) -> None | SampleRowBodyExpectedType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                expected_type_0 = SampleRowBodyExpectedType0.from_dict(data)

                return expected_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | SampleRowBodyExpectedType0 | Unset, data)

        expected = _parse_expected(d.pop("expected", UNSET))

        sample_row_body = cls(
            state=state,
            expected=expected,
        )

        return sample_row_body
