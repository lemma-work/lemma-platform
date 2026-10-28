"""A widget's `data` written as JSON text still reaches it as an object."""

from __future__ import annotations

import pytest

from app.modules.agent.tools.user_interaction.models import (
    DisplayResourceRequest,
    widget_data,
)

pytestmark = pytest.mark.unit


def test_an_object_written_as_its_json_text_is_shown_as_the_object() -> None:
    shown = DisplayResourceRequest(
        type="WIDGET",
        path="/skills/lemma-widget/library/show/note.html",
        data='{"title": "Venues", "markdown": "# Three"}',
    )
    assert shown.data == {"title": "Venues", "markdown": "# Three"}


@pytest.mark.parametrize(
    ("given", "read"),
    [
        ('[{"a": 1}]', [{"a": 1}]),
        ("just words", "just words"),
        ("{not json", "{not json"),
        ('"a quoted string"', '"a quoted string"'),
        ({"already": "an object"}, {"already": "an object"}),
        (None, None),
    ],
)
def test_only_text_that_is_an_object_or_a_list_is_decoded(
    given: object, read: object
) -> None:
    assert widget_data(given) == read
