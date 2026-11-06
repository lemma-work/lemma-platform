"""Push the runtime overlay to a connected machine, down one of its streams.

The same bundle ``workspace_runtime_bundle`` installs into sandboxes, so a
connected machine runs this server's runtime, not whatever it had when it was
joined. A sandbox is written to through its runtime's file API; a machine is
pushed to on a stream of its own (target ``runtime_bundle``), because the
machine may have no runtime running yet -- the first push is how it gets one.

The machine checks the archive's digest, installs it with
``sandbox_runtime.runtime_install`` under its own overlay root, restarts its
runtime, answers one JSON line and reports the new version on its next
``control``. Failure degrades: the machine keeps serving what it has, and the
next link tries again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.log.log import get_logger
from app.modules.agent.contracts.compute_link import (
    RuntimeBundleTarget,
    StreamTargetKind,
)
from app.modules.workspace.infrastructure.runtime_bundle import runtime_bundle
from app.modules.workspace.services.machine_link.routes import read_small_json
from sandbox_runtime.machine_link.mux import MAX_DATA_BYTES, StreamReset

if TYPE_CHECKING:
    from app.modules.workspace.services.machine_link.session import (
        ComputeLinkSession,
    )

logger = get_logger(__name__)

#: The most of an answer read back; it is one line of JSON.
_MAX_ANSWER_BYTES = 64 * 1024


def bundle_version() -> str | None:
    bundle = runtime_bundle()
    return bundle.version if bundle is not None else None


async def push_runtime(session: ComputeLinkSession) -> None:
    bundle = runtime_bundle()
    report = session.report
    if bundle is None or report is None or report.runtime_version == bundle.version:
        return
    target = RuntimeBundleTarget(
        kind=StreamTargetKind.RUNTIME_BUNDLE,
        version=bundle.version,
        sha256=bundle.archive_sha256,
        size=bundle.size_bytes,
        requires=list(bundle.requires),
    )
    connection_id = str(session.connection_id)
    try:
        stream = await session.open_stream(target.model_dump(mode="json"))
        try:
            archive = memoryview(bundle.archive)
            for offset in range(0, len(archive), MAX_DATA_BYTES):
                await stream.write(bytes(archive[offset : offset + MAX_DATA_BYTES]))
            await stream.write_eof()
            answer = bytearray()
            while chunk := await stream.read():
                answer.extend(chunk)
                if len(answer) > _MAX_ANSWER_BYTES:
                    break
        finally:
            await stream.aclose()
    except StreamReset as reset:
        logger.warning(
            "workspace.machine_link.runtime_push_failed.degraded",
            connection_id=connection_id,
            version=bundle.version,
            reset_code=reset.code,
            reset_message=reset.message,
        )
        return
    try:
        outcome = read_small_json(bytes(answer) or b"{}")
    except ValueError:
        outcome = {}
    logger.info(
        "workspace.machine_link.runtime_pushed",
        connection_id=connection_id,
        version=bundle.version,
        from_version=report.runtime_version,
        installed=bool(outcome.get("installed"))
        if isinstance(outcome, dict)
        else False,
    )
