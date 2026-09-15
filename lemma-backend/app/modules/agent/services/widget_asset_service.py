"""Resolve a conversation widget's HTML for serving.

A widget names its source in the display_resource tool call, addressed by
``(conversation_id, tool_call_id)``: either an inline ``content`` fragment, or
the ``path`` of a pod file the agent wrote. This service reads that call for the
source and the conversation's ``pod_id`` so the widget route can wrap, inject
pod context, and serve it — the same primitive as an app.

A path is read late, in ``resolve``, as the viewer. Reading it here would mean
reading a pod file before anyone had been authorized to see it, and would freeze
a widget whose whole advantage is that editing its file changes what it shows.
"""

from __future__ import annotations

from uuid import UUID

from collections.abc import Callable
from dataclasses import replace

from app.core.authorization.context import Context
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.ports.widget_content import (
    WidgetArtifact,
    WidgetContentReader,
    WidgetSourceUnavailable,
)
from app.modules.agent.infrastructure.widget_asset_repository import (
    WidgetAssetRepository,
)
from app.modules.datastore.contracts.agent_tools import (
    DatastoreFileService,
    build_file_service,
)


FileServiceFactory = Callable[[SqlAlchemyUnitOfWork], DatastoreFileService]


class WidgetAssetService(WidgetContentReader):
    """Both collaborators arrive through the constructor.

    The file service is reached through a factory rather than built inline so a
    test can hand one in. Patching the name in this module instead would put the
    double *inside* the subject: the one thing this service does with a path is
    read it as the viewer, and a stub installed over the factory certifies that
    for itself and survives any rename behind it.

    ``None`` rather than ``build_file_service`` as the default, and resolved at
    call time: a default argument is evaluated once at import, so binding the
    function here would quietly outlive anyone who replaced the module name
    afterwards -- and three other modules do replace it.
    """

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        repository: WidgetAssetRepository | None = None,
        file_service: FileServiceFactory | None = None,
    ):
        self._repo = repository or WidgetAssetRepository(uow)
        self._uow = uow
        self._file_service = file_service

    async def get_widget(
        self, conversation_id: UUID, tool_call_id: str
    ) -> WidgetArtifact | None:
        """Return the widget's source + pod context, or None if not found.

        Looks up the display_resource tool call by ``(conversation_id, tool_call_id)``
        and reads whichever source it names — an inline ``content`` fragment or
        the ``path`` of a pod file. The same id may appear on the tool-call and
        tool-return rows, so we scan for whichever row carries one.
        """
        rows = await self._repo.get_tool_args_for_call(
            conversation_id=conversation_id, tool_call_id=tool_call_id
        )

        content: str | None = None
        path: str | None = None
        title = ""
        for tool_args in rows:
            inline = tool_args.get("content")
            source = tool_args.get("path")
            # A call carries one or the other; whichever it is, the row that
            # carries it is the row this widget was authored in.
            if isinstance(inline, str) and inline.strip():
                content = inline
            elif isinstance(source, str) and source.strip():
                path = source.strip()
            else:
                continue
            name = tool_args.get("name")
            if isinstance(name, str):
                title = name
            break

        if content is None and path is None:
            return None

        pod_id = await self._repo.get_conversation_pod_id(conversation_id)
        if pod_id is None:
            return None

        return WidgetArtifact(
            content=content or "", pod_id=pod_id, title=title, path=path
        )

    async def resolve(self, artifact: WidgetArtifact, ctx: object) -> WidgetArtifact:
        """Read a path-backed widget's file as ``ctx``; inline artifacts pass through."""
        if not artifact.path:
            return artifact
        if not isinstance(ctx, Context):
            raise WidgetSourceUnavailable("A widget file needs an authorized reader.")
        files = self._file_service or build_file_service
        try:
            _, raw = await files(self._uow).download_file_content_by_path(
                artifact.pod_id, artifact.path, ctx
            )
        except DomainError as problem:
            # The file moved, was deleted, or this viewer cannot read it. All
            # three arrive as a DomainError and all three mean "there is no
            # widget here for you", so none of them spills a reason into a
            # served page. Anything else -- storage being down -- is not a
            # missing widget and is left to surface as the failure it is.
            raise WidgetSourceUnavailable(
                f"The widget's source file is not readable at '{artifact.path}'."
            ) from problem
        try:
            return replace(artifact, content=raw.decode("utf-8"))
        except UnicodeDecodeError as problem:
            raise WidgetSourceUnavailable(
                f"'{artifact.path}' is not text, so it cannot be a widget."
            ) from problem
