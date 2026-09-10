"""An agent can actually look at an image, on a real provider, under a budget.

Both routes are here because a monetary limit broke both and neither failure
mentioned an image. The delegate -- a text-only model's only way to see -- was
refused before it reached the provider, because its metering scope made its
first and only request look like a run *starting* with an unpriceable shape.
A model reading the image itself got through, then died at the request after
it, reconciling spend that had been recorded unpriced.

Real provider on purpose: the shape of the request is what was being judged,
and a stand-in that accepts `BinaryContent` proves nothing about a model that
has to price the tokens it turns into.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from PIL import ImageFont
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from sqlalchemy import select

from app.core.infrastructure.db.manager import DatabaseManager
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.services.vision_service import (
    VisionImage,
    configured_vision_model_name,
    describe_images,
)
from app.modules.agent.tests.e2e.system_lemma_helpers import (
    SYSTEM_LEMMA_SKIP_REASON,
    system_lemma_available,
)
from app.modules.usage.config import usage_settings
from app.modules.usage.infrastructure.metered_model import MeteredModel
from app.modules.usage.infrastructure.models import UsageRecord
from app.modules.usage.services.metering_scope import metering_execution
from app.modules.usage.services.usage_context import UsageExecutionContext

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.real_llm,
    pytest.mark.provider,
    pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON),
]

# Short, unambiguous, and nothing a model could produce from the prompt alone.
SECRET = "PLUM 47"


def _legible_font(size: int) -> "ImageFont.FreeTypeFont | ImageFont.ImageFont":
    """A face big enough to read, on whatever machine is running this.

    `load_default` is the one that cannot fail, and since Pillow 10.1 it takes
    a size and returns a scalable face -- so the fallback is still legible
    rather than the 11px bitmap that made this test depend on a font macOS
    happens to ship.
    """
    for name in (
        "Helvetica",
        "DejaVuSans.ttf",
        "Arial.ttf",
        "LiberationSans-Regular.ttf",
    ):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _image_saying(text: str) -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (640, 200), "white")
    ImageDraw.Draw(image).text((40, 60), text, fill="black", font=_legible_font(56))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def vision_model_name(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """The deployment's configured vision model, declared image-capable.

    Declared here rather than assumed: `LEMMA_OPENAI_VISION_MODEL_NAMES` is the
    operator's statement about a catalog, and this test is about the code path
    that carries an image, not about one deployment's environment file.
    """
    name = configured_vision_model_name()
    if not name:
        pytest.skip("VISION_MODEL is not configured.")
    monkeypatch.setenv("LEMMA_OPENAI_VISION_MODEL_NAMES", name)
    yield name


@pytest.fixture
def spending_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """A monetary limit with plenty of room -- enough to engage the gate."""
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 100.0)


async def _records(db_manager: DatabaseManager, user_id: UUID) -> list[UsageRecord]:
    async with db_manager.session_factory() as session:
        return list(
            await session.scalars(
                select(UsageRecord)
                .where(UsageRecord.user_id == user_id)
                .order_by(UsageRecord.occurred_at)
            )
        )


async def test_a_text_only_model_sees_through_its_delegate(
    db_manager: DatabaseManager, vision_model_name: str, spending_budget: None
) -> None:
    """`describe_images` from inside a run that is already under way."""
    user_id = uuid4()
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    run = UsageExecutionContext(
        user_id=user_id, organization_id=None, pod_id=None, agent_run_id=uuid4()
    )
    async with metering_execution(run, factory=factory) as scope:
        # The run has already reached the provider once; a tool call is the
        # only thing that can ask for this, and a tool call implies a turn.
        meter, _ = scope.meter(
            {"profile_id": "system:lemma", "scope": "SYSTEM", "model_name": "opening"},
            None,
        )
        meter.admitted = 1

        description = await describe_images(
            [
                VisionImage(
                    data=_image_saying(SECRET),
                    media_type="image/png",
                    label="image /pod/note.png",
                )
            ],
            instructions="Transcribe the text in this image exactly.",
            organization_id=None,
            user_id=user_id,
        )

    assert SECRET.split()[0].lower() in description.lower(), description
    assert SECRET.split()[1] in description, description

    vision = await _records(db_manager, user_id)
    assert vision, "the delegate's request was never metered"
    assert {record.source_type for record in vision} == {"vision"}
    # Priced, not merely admitted: the provider counted the pixels as input
    # tokens and the rate card charged them.
    assert all(record.input_tokens for record in vision)


async def test_a_vision_model_reads_the_image_and_the_run_carries_on(
    db_manager: DatabaseManager, vision_model_name: str, spending_budget: None
) -> None:
    """What `view_image` does when the run's own model can see."""
    from app.core.domain.runtime import AgentRuntimeConfig
    from app.modules.agent.services.runtime_model_factory import (
        pydantic_ai_model_from_runtime_profile,
    )
    from app.modules.agent.services.runtime_profile_service import (
        DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
        AgentRuntimeProfileService,
    )

    user_id = uuid4()
    resolved = await AgentRuntimeProfileService().resolve(
        runtime=AgentRuntimeConfig(
            profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
            model_name=vision_model_name,
        ),
        organization_id=None,
        user_id=user_id,
    )
    # Already metered: the factory wraps what it builds, exactly as a run gets it.
    model = pydantic_ai_model_from_runtime_profile(
        runtime_profile=resolved.public_snapshot(),
        runtime_credentials=resolved.credentials,
    )
    assert isinstance(model, MeteredModel)
    assert model.runtime_profile.get("scope") == "SYSTEM", (
        "only a SYSTEM profile is held to a monetary limit; this test needs one"
    )

    # The history a run has after `view_image` returned the picture itself.
    looking: list[ModelMessage] = [
        ModelRequest(
            parts=[UserPromptPart(["Read /pod/note.png and repeat its text exactly."])]
        ),
        ModelResponse(
            parts=[
                ToolCallPart(
                    "view_image", {"file_path": "/pod/note.png"}, tool_call_id="call-1"
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "view_image",
                    [BinaryContent(data=_image_saying(SECRET), media_type="image/png")],
                    tool_call_id="call-1",
                )
            ]
        ),
    ]

    async with metering_execution(
        UsageExecutionContext(user_id=user_id, organization_id=None, pod_id=None),
        factory=SessionUnitOfWorkFactory(db_manager.session_factory),
    ):
        seen = await model.request(looking, None, ModelRequestParameters())
        # The request after the image is the one that used to die -- and it
        # replays the image, because the history still holds it.
        await model.request(
            [*looking, seen, ModelRequest(parts=[UserPromptPart(["Now say OK."])])],
            None,
            ModelRequestParameters(),
        )

    answer = "".join(part.content for part in seen.parts if isinstance(part, TextPart))
    assert SECRET.split()[1] in answer, answer

    records = await _records(db_manager, user_id)
    assert len(records) == 2
    # Priced, both of them. Recording the image request unpriced is what set
    # `require_reconciliation` and killed the one after it.
    assert all(record.cost_amount is not None for record in records)
    assert all(record.input_tokens for record in records)
    assert sum(record.cost_amount or Decimal(0) for record in records) > 0
