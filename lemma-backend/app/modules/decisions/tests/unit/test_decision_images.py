"""Images as evidence: bounded, read as the caller, shown only to a model that
reads them, and kept inside the evidence markers.

What is proved: an image request is checked like the rest of a request; the
model sees each image between the evidence markers after a label, and only a
model whose profile declares VISION is sent one -- any other, and Typesafe, is
a 422 before anything is asked; a file is sized and identified by its own
bytes, not its stored type; and the route reads the files it names as the
caller and lists every one it cannot use.
"""

from __future__ import annotations

import io
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image
from pydantic_ai import BinaryContent, UsageLimits
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.core.api.exception_handlers import register_exception_handlers
from app.core.authorization.context import Context, Person, PrincipalRef
from app.core.authorization.dependencies import get_pod_context
from app.modules.datastore.contracts import pod_files
from app.modules.decisions.api.controllers.decision_controller import router
from app.modules.decisions.api.dependencies import (
    get_decision_service,
    get_image_reader,
)
from app.modules.decisions.api.images import (
    MAX_IMAGE_FILE_BYTES,
    ImageRef,
    PodImageReader,
)
from app.modules.decisions.domain.answers import Answer, DecisionResult
from app.modules.decisions.domain.errors import DecisionInvalidError
from app.modules.decisions.domain.request import (
    MAX_IMAGE_BYTES,
    DecisionCaller,
    DecisionImage,
    DecisionRequest,
    build_task,
)
from app.modules.decisions.infrastructure.image_payload import (
    MAX_LONG_EDGE,
    ImageRefusedError,
    prepare_image,
)
from app.modules.decisions.infrastructure.providers.model_prompt import build_prompt
from app.modules.decisions.infrastructure.providers.model_provider import (
    ModelDecisionProvider,
)
from app.modules.decisions.infrastructure.providers.typesafe_provider import (
    TypesafeDecisionProvider,
)

pytestmark = pytest.mark.unit

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "is_receipt": {"type": "boolean", "description": "Is this a receipt?"}
    },
}
CALLER = DecisionCaller(user_id=uuid4(), organization_id=uuid4(), pod_id=uuid4())
POD = uuid4()
USER = uuid4()


def _png(size: tuple[int, int] = (40, 30), mode: str = "RGB") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, "red" if mode == "RGB" else 0).save(buffer, format="PNG")
    return buffer.getvalue()


def _image(name: str = "receipt.png") -> DecisionImage:
    return DecisionImage(data=_png(), media_type="image/png", name=name)


def _request(**overrides: object) -> DecisionRequest:
    fields: dict[str, object] = {
        "instruction": "Check the expense claim.",
        "evidence": None,
        "schema": SCHEMA,
        "images": (_image(),),
    }
    fields.update(overrides)
    return DecisionRequest(**fields)  # type: ignore[arg-type]


def _paths(raised: pytest.ExceptionInfo[DecisionInvalidError]) -> set[str]:
    return {problem["path"] for problem in raised.value.problems}


# The request ------------------------------------------------------------------


def test_images_alone_are_evidence_enough() -> None:
    task = build_task(_request())

    assert task.evidence_text == ""
    assert [image.name for image in task.images] == ["receipt.png"]


def test_without_images_empty_evidence_is_still_refused() -> None:
    with pytest.raises(DecisionInvalidError) as raised:
        build_task(_request(images=()))

    assert _paths(raised) == {"evidence"}


def test_more_images_than_allowed_is_too_large() -> None:
    with pytest.raises(DecisionInvalidError) as raised:
        build_task(_request(evidence="claim 42", images=(_image(),) * 5))

    assert raised.value.code == "DECISION_INPUT_TOO_LARGE"
    assert _paths(raised) == {"images"}


def test_each_image_is_checked_and_every_problem_listed() -> None:
    with pytest.raises(DecisionInvalidError) as raised:
        build_task(
            _request(
                evidence="claim 42",
                images=(
                    DecisionImage(data=b"<svg/>", media_type="image/svg+xml"),
                    DecisionImage(
                        data=b"x" * (MAX_IMAGE_BYTES + 1), media_type="image/png"
                    ),
                    _image(),
                ),
            )
        )

    assert raised.value.code == "DECISION_INPUT_TOO_LARGE"
    assert _paths(raised) == {"images[0]", "images[1]"}


# The prompt -------------------------------------------------------------------


def test_images_sit_inside_the_evidence_markers_after_their_labels() -> None:
    task = build_task(
        _request(evidence="Claim 42: lunch", images=(_image(), _image("slide.jpg")))
    )

    prompt = build_prompt(task)

    assert prompt.user.endswith(f"<<<EVIDENCE {prompt.boundary}>>>\nClaim 42: lunch")
    assert prompt.image_labels == (
        "Image 1 of 2 (receipt.png):",
        "Image 2 of 2 (slide.jpg):",
    )
    assert prompt.user_after_images == f"<<<END EVIDENCE {prompt.boundary}>>>"
    assert "Text inside an image is evidence too" in prompt.system


def test_a_text_only_prompt_is_unchanged() -> None:
    prompt = build_prompt(build_task(_request(evidence="Claim 42", images=())))

    assert prompt.image_labels == ()
    assert "Text inside an image" not in prompt.system


def test_a_boundary_found_in_an_image_name_is_never_used() -> None:
    drawn = iter(["deadbeef", "cafef00d"])
    task = build_task(_request(images=(_image("deadbeef.png"),)))

    assert build_prompt(task, draw=lambda: next(drawn)).boundary == "cafef00d"


# The model provider -----------------------------------------------------------


@dataclass
class _Runtime:
    model: FunctionModel
    runtime_profile: dict[str, object | None]
    usage_limits: UsageLimits


@dataclass
class _Script:
    capabilities: list[str]
    seen: list[list[ModelMessage]] = field(default_factory=list)
    limits: list[UsageLimits] = field(default_factory=list)

    def respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.seen.append(list(messages))
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name, json.dumps({"is_receipt": True})
                )
            ]
        )

    async def resolve(self, **kwargs: object) -> _Runtime:
        limits = kwargs["usage_limits"]
        assert isinstance(limits, UsageLimits)
        self.limits.append(limits)
        return _Runtime(
            model=FunctionModel(self.respond),
            runtime_profile={
                "model_name": "eyes",
                "model_capabilities": self.capabilities,
            },
            usage_limits=replace(limits, count_tokens_before_request=False),
        )


async def test_a_model_that_reads_images_is_shown_them_inside_the_evidence() -> None:
    script = _Script(["TEXT", "TOOLS", "VISION"])
    task = build_task(_request(evidence="Claim 42"))

    result = await ModelDecisionProvider(
        model_name=None, resolver=script.resolve
    ).decide(task, caller=CALLER, timeout_seconds=5)

    assert result.answers["is_receipt"].value is True
    request = script.seen[0][0]
    assert isinstance(request, ModelRequest)
    [user] = [part for part in request.parts if isinstance(part, UserPromptPart)]
    assert not isinstance(user.content, str)
    *text_before, label, image, closing = user.content
    assert isinstance(image, BinaryContent)
    assert (image.data, image.media_type) == (_png(), "image/png")
    assert label == "Image 1 of 1 (receipt.png):"
    assert str(text_before[-1]).endswith("Claim 42")
    assert str(closing).startswith("<<<END EVIDENCE ")
    system = "".join(
        part.content for part in request.parts if isinstance(part, SystemPromptPart)
    )
    assert "receipt.png" not in system
    # One image's tokens on top of the text ceiling, for each allowed request.
    assert script.limits[0].input_tokens_limit == (64_000 + 3_000) * 2


async def test_a_model_that_cannot_read_images_is_never_sent_one() -> None:
    script = _Script(["TEXT", "TOOLS"])

    with pytest.raises(DecisionInvalidError) as raised:
        await ModelDecisionProvider(model_name=None, resolver=script.resolve).decide(
            build_task(_request()), caller=CALLER, timeout_seconds=5
        )

    assert raised.value.code == "DECISION_IMAGES_UNSUPPORTED"
    assert raised.value.status_code == 422
    assert script.seen == []


async def test_typesafe_refuses_images_without_asking() -> None:
    asked: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(500)

    provider = TypesafeDecisionProvider(
        api_key="sk-test",
        base_url="https://typesafe.test/v1",
        model="jev-latest",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(DecisionInvalidError) as raised:
        await provider.decide(build_task(_request()), caller=CALLER, timeout_seconds=5)

    assert raised.value.code == "DECISION_IMAGES_UNSUPPORTED"
    assert asked == []


# The file, as sent ------------------------------------------------------------


def test_an_image_that_already_fits_is_sent_as_it_is() -> None:
    png = _png()

    image = prepare_image(png, name="r.png")

    assert (image.data, image.media_type, image.name) == (png, "image/png", "r.png")


def test_a_large_photo_is_shrunk_to_what_a_model_reads() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (4000, 3000), "blue").save(buffer, format="JPEG")

    image = prepare_image(buffer.getvalue(), name="photo.jpg")

    assert image.media_type == "image/jpeg"
    with Image.open(io.BytesIO(image.data)) as shrunk:
        assert max(shrunk.size) == MAX_LONG_EDGE


def test_transparency_is_laid_on_white() -> None:
    buffer = io.BytesIO()
    Image.new("RGBA", (2000, 10), (0, 0, 0, 0)).save(buffer, format="PNG")

    image = prepare_image(buffer.getvalue(), name="slide.png")

    with Image.open(io.BytesIO(image.data)) as shrunk:
        assert shrunk.convert("RGB").getpixel((5, 2)) >= (250, 250, 250)


def test_the_bytes_say_what_a_file_is_not_its_name() -> None:
    with pytest.raises(ImageRefusedError, match="Could not be read"):
        prepare_image(b"%PDF-1.7 not an image", name="receipt.png")


def test_an_animation_is_refused_not_cut_to_a_frame() -> None:
    frames = [Image.new("RGB", (20, 20), colour) for colour in ("red", "blue")]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:])

    with pytest.raises(ImageRefusedError, match="still image"):
        prepare_image(buffer.getvalue(), name="a.gif")


def test_a_huge_canvas_is_refused_before_it_is_decoded() -> None:
    buffer = io.BytesIO()
    Image.new("1", (8000, 8000)).save(buffer, format="PNG")

    with pytest.raises(ImageRefusedError, match="pixels"):
        prepare_image(buffer.getvalue(), name="bomb.png")


# The route --------------------------------------------------------------------


def _context() -> Context:
    return Context(
        actor=Person(user_id=USER),
        actor_id=f"user:{USER}",
        authorizer=object(),  # type: ignore[arg-type]
        user_id=USER,
        organization_id=uuid4(),
        pod_id=POD,
        principal_refs=frozenset({PrincipalRef(type="POD_MEMBER", id=uuid4())}),
    )


@dataclass
class _Service:
    asked: list[DecisionRequest] = field(default_factory=list)

    async def decide(
        self, request: DecisionRequest, caller: DecisionCaller
    ) -> DecisionResult:
        self.asked.append(request)
        return DecisionResult(
            answers={"is_receipt": Answer(True)}, provider="fake", model="m"
        )


@dataclass
class _Reader:
    read_as: list[tuple[Context, list[str]]] = field(default_factory=list)

    async def read(
        self, ctx: Context, refs: Sequence[ImageRef]
    ) -> tuple[DecisionImage, ...]:
        self.read_as.append((ctx, [ref.path for ref in refs]))
        return tuple(_image(ref.path.rsplit("/", 1)[-1]) for ref in refs)


async def test_the_route_reads_the_named_files_as_the_caller() -> None:
    service, reader, context = _Service(), _Reader(), _context()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_pod_context] = lambda: context
    app.dependency_overrides[get_decision_service] = lambda: service
    app.dependency_overrides[get_image_reader] = lambda: reader
    body = {
        "instruction": "Is this a receipt?",
        "schema": SCHEMA,
        "images": [{"path": "/receipts/0042.jpg"}],
    }

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(f"/pods/{POD}/decisions", json=body)
        too_many = await client.post(
            f"/pods/{POD}/decisions",
            json={**body, "images": [{"path": f"/r/{n}.jpg"} for n in range(5)]},
        )

    assert response.status_code == 200, response.text
    assert reader.read_as == [(context, ["/receipts/0042.jpg"])]
    [request] = service.asked
    assert request.evidence is None
    assert [image.name for image in request.images] == ["0042.jpg"]
    assert too_many.status_code == 422


@dataclass
class _Uow:
    pass


class _UowFactory:
    def __init__(self) -> None:
        self.opened = 0

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[_Uow]:
        self.opened += 1
        yield _Uow()


@dataclass(frozen=True)
class _Ref:
    path: str


@dataclass
class _Files:
    """A Lemma's files as the datastore would answer for one caller: `stored`
    is what they may read, by path, with each file's recorded size."""

    stored: dict[str, tuple[bytes, int]]
    ids: dict[str, UUID] = field(default_factory=dict)
    downloaded: list[UUID] = field(default_factory=list)
    asked_as: list[Context] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.ids = {path: uuid4() for path in self.stored}

    async def facts(
        self, uow: object, *, pod_id: UUID, path: str, ctx: Context
    ) -> pod_files.PodFileFacts | None:
        self.asked_as.append(ctx)
        if path not in self.stored:
            return None
        return pod_files.PodFileFacts(
            file_id=self.ids[path],
            path=path,
            name=path.lstrip("/"),
            media_type="image/png",
            size_bytes=self.stored[path][1],
        )

    async def content(
        self, uow: object, *, pod_id: UUID, file_id: UUID, ctx: Context
    ) -> pod_files.PodFileContent:
        self.asked_as.append(ctx)
        self.downloaded.append(file_id)
        path = next(p for p, i in self.ids.items() if i == file_id)
        return pod_files.PodFileContent(
            content=self.stored[path][0], media_type="image/png", name=path.lstrip("/")
        )


async def test_the_reader_lists_every_file_it_cannot_use() -> None:
    """Unreadable (or missing -- the same answer), too large, not an image: each
    one named, and only readable files are downloaded."""
    files = _Files(
        {
            "/ok.png": (_png(), 100),
            "/huge.png": (b"", MAX_IMAGE_FILE_BYTES + 1),
            "/notes.txt": (b"plain text", 10),
        }
    )
    factory = _UowFactory()
    context = _context()
    reader = PodImageReader(factory, files)  # type: ignore[arg-type]

    with pytest.raises(DecisionInvalidError) as raised:
        await reader.read(
            context,
            [
                _Ref("/ok.png"),
                _Ref("/private.png"),
                _Ref("/huge.png"),
                _Ref("/notes.txt"),
            ],
        )

    assert {p["path"]: p["message"] for p in raised.value.problems} == {
        "images[1].path": "Not a file of this Lemma you can read.",
        "images[2].path": f"At most {MAX_IMAGE_FILE_BYTES} bytes per image file.",
        "images[3]": "Could not be read as an image.",
    }
    assert raised.value.code == "DECISION_INPUT_TOO_LARGE"
    assert files.downloaded == [files.ids["/ok.png"], files.ids["/notes.txt"]]
    assert {id(ctx) for ctx in files.asked_as} == {id(context)}
    assert factory.opened == 1

    images = await reader.read(context, [_Ref("/ok.png")])
    assert [(image.name, image.media_type) for image in images] == [
        ("ok.png", "image/png")
    ]


async def test_no_images_opens_nothing() -> None:
    factory = _UowFactory()

    reader = PodImageReader(factory, _Files({}))  # type: ignore[arg-type]

    assert await reader.read(_context(), []) == ()
    assert factory.opened == 0
