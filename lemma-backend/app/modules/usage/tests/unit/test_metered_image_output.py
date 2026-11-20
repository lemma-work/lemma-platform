"""A request that draws is counted in image tokens and in images.

Google files an image model's output under `image_candidates_tokens`, inside the
ordinary output total; the image rate applies only if that share is split out.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic_ai.messages import BinaryImage, FilePart, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.usage import RequestUsage

from app.modules.usage.infrastructure.metered_model import Dispatch, _images_in
from app.modules.usage.infrastructure.price_catalog import Rate, RateCard

pytestmark = pytest.mark.unit

_CARD = RateCard(
    model="image-model",
    enforceable=True,
    rates={
        "input_mtok": Rate(base=Decimal("0.25")),
        "output_mtok": Rate(base=Decimal("1.5")),
        "output_image_mtok": Rate(base=Decimal("30")),
    },
)


def _dispatch(usage: RequestUsage, response: ModelResponse) -> Dispatch:
    dispatch = Dispatch(
        {}, uuid4(), datetime.now(timezone.utc), ModelRequestParameters()
    )
    dispatch.usage = usage
    dispatch.images = _images_in(response)
    return dispatch


def test_a_drawn_image_is_counted_and_priced_as_one() -> None:
    picture = BinaryImage(data=b"\x89PNG", media_type="image/png")
    receipt = _dispatch(
        RequestUsage(
            input_tokens=11,
            output_tokens=1120,
            details={"image_candidates_tokens": 1120},
        ),
        ModelResponse(parts=[FilePart(content=picture), TextPart(content="Here")]),
    ).receipt(_CARD, priceable=True)

    assert receipt.counts.output_image_tokens == 1120
    assert receipt.counts.output_images == 1
    assert receipt.cost == Decimal("0.03360275")


def test_a_text_answer_draws_nothing() -> None:
    receipt = _dispatch(
        RequestUsage(input_tokens=10, output_tokens=5),
        ModelResponse(parts=[TextPart(content="ok")]),
    ).receipt(_CARD, priceable=True)

    assert receipt.counts.output_image_tokens == 0
    assert receipt.counts.output_images == 0
