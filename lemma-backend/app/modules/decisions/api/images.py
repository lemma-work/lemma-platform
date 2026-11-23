"""The images a decision request names: a Lemma's files, read as the caller.

A request names files by path; it never sends bytes or a URL. Bytes would put
megabytes of base64 in a JSON body the route bounds at 512 KiB, and a URL this
server fetched would let anyone who can ask a decision make it request any
address it can reach, internal ones included. A file of the Lemma is read with
the caller's own access -- a member's, or a function's or agent's grants -- the
same check every file read makes, so a decision can show a model nothing the
caller could not open themselves.

The files are read in one short unit of work that is closed before anything is
decoded or any model is asked. A file that cannot be used refuses the request,
listing every such file, before the rest of the request is checked.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from app.core.authorization.context import Context
from app.core.concurrency.offload import run_blocking
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.decisions.domain.errors import DecisionInvalidError
from app.modules.decisions.domain.request import DecisionImage

if TYPE_CHECKING:
    from app.modules.datastore.contracts.pod_files import (
        PodFileContent,
        PodFileFacts,
    )

#: Per file, before it is shrunk. A phone photo or a full-screen screenshot
#: fits; the cap bounds what one request holds in memory.
MAX_IMAGE_FILE_BYTES = 10 * 1024 * 1024


class ImageRef(Protocol):
    @property
    def path(self) -> str: ...


@dataclass(frozen=True, slots=True)
class _Read:
    index: int
    content: bytes
    name: str


@dataclass(frozen=True, slots=True)
class _Problem:
    path: str
    message: str
    too_large: bool = False


_UNREADABLE = "Not a file of this Lemma you can read."


class PodFileReads(Protocol):
    """The two file reads, each made with the caller's access."""

    async def facts(
        self, uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, path: str, ctx: Context
    ) -> PodFileFacts | None: ...

    async def content(
        self, uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, file_id: UUID, ctx: Context
    ) -> PodFileContent: ...


class DatastoreFileReads:
    """The datastore's own reads, through its published contract."""

    async def facts(
        self, uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, path: str, ctx: Context
    ) -> PodFileFacts | None:
        from app.modules.datastore.contracts.pod_files import readable_pod_file

        return await readable_pod_file(uow, pod_id=pod_id, path=path, ctx=ctx)

    async def content(
        self, uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, file_id: UUID, ctx: Context
    ) -> PodFileContent:
        from app.modules.datastore.contracts.pod_files import read_pod_file_by_id

        return await read_pod_file_by_id(uow, pod_id=pod_id, file_id=file_id, ctx=ctx)


class PodImageReader:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, files: PodFileReads | None = None
    ) -> None:
        self._uow_factory = uow_factory
        self._files_reads = files or DatastoreFileReads()

    async def read(
        self, ctx: Context, refs: Sequence[ImageRef]
    ) -> tuple[DecisionImage, ...]:
        if not refs:
            return ()
        if ctx.pod_id is None:
            raise DecisionInvalidError(
                [{"path": "images", "message": "Images are read from a Lemma."}]
            )
        problems: list[_Problem] = []
        files = await self._files(ctx, ctx.pod_id, refs, problems)
        prepared = await run_blocking(_prepare_all, files, limiter="cpu_bound")
        images: list[DecisionImage] = []
        for read, outcome in zip(files, prepared, strict=True):
            if isinstance(outcome, str):
                problems.append(_Problem(f"images[{read.index}]", outcome))
            else:
                images.append(outcome)
        if problems:
            problems.sort(key=lambda problem: problem.path)
            raise DecisionInvalidError(
                [{"path": p.path, "message": p.message} for p in problems],
                code=(
                    "DECISION_INPUT_TOO_LARGE"
                    if any(problem.too_large for problem in problems)
                    else "DECISION_INVALID_REQUEST"
                ),
            )
        return tuple(images)

    async def _files(
        self,
        ctx: Context,
        pod_id: UUID,
        refs: Sequence[ImageRef],
        problems: list[_Problem],
    ) -> list[_Read]:
        files: list[_Read] = []
        async with self._uow_factory() as uow:
            for index, ref in enumerate(refs):
                path = f"images[{index}].path"
                found = await self._files_reads.facts(
                    uow, pod_id=pod_id, path=ref.path, ctx=ctx
                )
                if found is None:
                    problems.append(_Problem(path, _UNREADABLE))
                    continue
                if found.size_bytes > MAX_IMAGE_FILE_BYTES:
                    problems.append(_too_large(path))
                    continue
                try:
                    read = await self._files_reads.content(
                        uow, pod_id=pod_id, file_id=found.file_id, ctx=ctx
                    )
                except DomainError as exc:
                    # Gone or no longer readable since it was found: the same
                    # answer as never readable. A storage failure is not the
                    # caller's to fix, and stays a server error.
                    if exc.status_code >= 500:
                        raise
                    problems.append(_Problem(path, _UNREADABLE))
                    continue
                if len(read.content) > MAX_IMAGE_FILE_BYTES:
                    problems.append(_too_large(path))
                    continue
                files.append(_Read(index=index, content=read.content, name=read.name))
        return files


def _too_large(path: str) -> _Problem:
    return _Problem(
        path, f"At most {MAX_IMAGE_FILE_BYTES} bytes per image file.", too_large=True
    )


def _prepare_all(files: Sequence[_Read]) -> list[DecisionImage | str]:
    from app.modules.decisions.infrastructure.image_payload import (
        ImageRefusedError,
        prepare_image,
    )

    outcomes: list[DecisionImage | str] = []
    for read in files:
        try:
            outcomes.append(prepare_image(read.content, name=read.name))
        except ImageRefusedError as refused:
            outcomes.append(str(refused))
    return outcomes
