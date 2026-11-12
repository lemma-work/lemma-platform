"""A size label, as a person or a bundle writes it: `<vCPUs>x<GB>`."""

from __future__ import annotations

import pytest

from app.modules.workspace.domain.machine import MachineSize


@pytest.mark.parametrize(
    ("label", "size"),
    [("2x4", MachineSize(2, 4)), (" 8X16 ", MachineSize(8, 16))],
)
def test_an_offered_size_parses(label: str, size: MachineSize) -> None:
    assert MachineSize.parse(label) == size


@pytest.mark.parametrize(
    "label",
    ["", "2x", "x4", "2x4x", "2 x4", "²x4", "1234x2", "3x3", "0" * 100_000],
)
def test_anything_else_is_refused(label: str) -> None:
    with pytest.raises(ValueError):
        MachineSize.parse(label)
