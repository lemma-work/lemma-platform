"""One bundle of a plugin install, as a work item: ``pod_bundle.install_plugin``.

Written with its import by ``InstallUseCases``, one per bundle, under the
import's plan key. The item fetches the bundle, plans it, approves the plan
with the install page's defaults (``domain/plugin_install.page_defaults``,
checked by the same ``approve_plan`` the page's apply goes through) and
applies it -- each half the ordinary import's own body, so a retried or
resumed item carries on from the state row: a plan already made is not made
again, and apply picks up at its first pending step.

Where the page would have asked a question, the import is withdrawn before
anything is applied (``CANCELLED``, with the question on its install slot) and
the next bundle goes ahead. Nobody has to be watching for any of it.

When the last bundle of the install settles, the item's completion tells the
person, in the same transaction: one note from the Lemma in their own chat
with it (``agent_surfaces.contracts.direct_messages.tell_member``), saying what
is ready and what stopped. A note is never pushed outside the app.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID

from obstore.exceptions import BaseError as ObjectStoreError

from app.core.authorization.scope import uow_scope
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import (
    DeadContext,
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.pod_bundle.domain.errors import BundleStateConflictError
from app.modules.pod_bundle.domain.plugin_install import (
    bundle_name,
    outcome_of,
    page_defaults,
    settled,
    settled_note,
)
from app.modules.pod_bundle.domain.state import ImportState, ImportStatus
from app.modules.pod_bundle.events.work import (
    IMPORT,
    INSTALL_PLUGIN,
    MAX_RUNTIME,
    RETRY,
    GithubImportJob,
    end_dead_job,
    run_body,
    worker_context,
)

logger = get_logger(__name__)


async def install_bundle(context: dict[str, str | None]) -> None:
    """Take one bundle of the install as far as it goes."""
    from app.modules.pod_bundle.events.handlers import (
        apply_pod_import,
        import_pod_github,
        plan_pod_import,
    )
    from app.modules.pod_bundle.infrastructure.state_store import (
        get_pod_bundle_state_store,
    )

    store = get_pod_bundle_state_store()
    import_id = UUID(str(context["import_id"]))
    state = await store.get_import(import_id)
    if state is None or state.is_terminal:
        return
    if state.plan is None:
        if state.status is ImportStatus.PLANNING:  # staged, then interrupted
            await plan_pod_import(context)
        else:
            await import_pod_github(context)
        state = await store.get_import(import_id)
        if state is None or state.status is not ImportStatus.AWAITING_CONFIRMATION:
            return
    if state.status is ImportStatus.AWAITING_CONFIRMATION:
        if not await _approve_with_page_defaults(state):
            return
    await apply_pod_import(context)


async def _approve_with_page_defaults(state: ImportState) -> bool:
    """Approve the plan as the page would with nobody choosing; ``False``
    when the import was withdrawn instead, or somebody else ended it."""
    from app.modules.pod_bundle.application.approval import approve_plan
    from app.modules.pod_bundle.infrastructure.state_store import (
        get_pod_bundle_state_store,
    )

    assert state.plan is not None, "a planned import"
    uow_factory = worker_context().uow_factory
    defaults = page_defaults(state.plan, await _lemma_name(state))
    try:
        if isinstance(defaults, str):
            await _withdraw(state, defaults)
            return False
        try:
            await approve_plan(
                uow_factory,
                state,
                user_id=state.user_id,
                variables=defaults.variables,
                confirm_destructive=False,
                connectors=defaults.connectors,
            )
        except DomainError as refused:
            await _withdraw(state, str(refused))
            return False
        await get_pod_bundle_state_store().save_import(state)
    except BundleStateConflictError:
        # Cancelled while it was being approved: the cancel stands.
        return False
    return True


async def _lemma_name(state: ImportState) -> str:
    """The Lemma's name, which the page derives a required name from; the
    bundle's own when the Lemma's is gone."""
    from app.modules.pod.contracts.members import pod_name

    async with uow_scope(worker_context().uow_factory) as uow:
        name = await pod_name(uow.session, state.pod_id)
    return name or bundle_name(state)


async def _withdraw(state: ImportState, question: str) -> None:
    """Stop before applying anything, keeping the question the page would ask."""
    from app.modules.pod_bundle.infrastructure.realtime import (
        completed_payload,
        publish_bundle_event,
    )
    from app.modules.pod_bundle.infrastructure.staging import BundleStagingStorage
    from app.modules.pod_bundle.infrastructure.state_store import (
        get_pod_bundle_state_store,
    )

    assert state.install is not None, "an install's import"
    state.install = state.install.model_copy(update={"asks": question})
    state.status = ImportStatus.CANCELLED
    state.current_step = None
    state.completed_at = datetime.now(timezone.utc)
    await get_pod_bundle_state_store().save_import(state)
    await publish_bundle_event(
        state.import_id, completed_payload(state.status.value, state.seq)
    )
    try:
        await BundleStagingStorage().delete_archive("pod-imports", state.import_id)
    except ObjectStoreError:
        logger.debug(
            "pod_bundle.plugin_install.clean_staging_withdrawn_import.diagnostic",
            import_id=str(state.import_id),
        )


async def tell_if_settled(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, import_id: UUID
) -> None:
    """Once every bundle of this import's install has settled, tell the person.

    Read on ``uow``, so a dead item's hook sees the import it has just ended.
    Keyed by what each bundle came to, so a re-run of the same outcome says
    nothing twice, while a retry that changes it is told again.
    """
    from app.modules.agent_surfaces.contracts.direct_messages import tell_member
    from app.modules.pod_bundle.infrastructure.install_reads import (
        imports_of_install,
    )
    from app.modules.pod_bundle.infrastructure.models import PodBundleJob

    row = await uow.session.get(PodBundleJob, import_id)
    install = None if row is None else ImportState.model_validate(row.snapshot).install
    if install is None:
        return
    states = await imports_of_install(
        pod_id=pod_id, install_id=install.install_id, session=uow.session
    )
    if not states or not settled(states):
        return
    outcome = ";".join(
        f"{s.import_id}:{outcome_of(s).value}:{s.attempt}" for s in states
    )
    digest = hashlib.sha256(outcome.encode()).hexdigest()[:8]
    await tell_member(
        uow,
        pod_id=pod_id,
        user_id=states[0].user_id,
        text=settled_note(states),
        key=f"plugin-install:{install.install_id}:{digest}",
    )
    logger.info(
        "pod_bundle.plugin_install.settled",
        pod_id=str(pod_id),
        install_id=str(install.install_id),
        bundles=len(states),
    )


async def run_install_plugin(ctx: JobContext, payload: GithubImportJob) -> None:
    await run_body(ctx, payload, install_bundle, IMPORT)
    async with ctx.complete_work() as uow:
        await tell_if_settled(uow, pod_id=payload.pod_id, import_id=payload.import_id)


async def end_dead_install_plugin(ctx: DeadContext) -> None:
    """End the import as any dead bundle job's is, and tell the person if
    that settled the install."""
    await end_dead_job(ctx)
    payload = GithubImportJob.model_validate(ctx.item.payload)
    await tell_if_settled(ctx.uow, pod_id=payload.pod_id, import_id=payload.import_id)


def register_install_plugin_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=INSTALL_PLUGIN,
        cls=WorkClass.ADMIN,
        mode=WorkMode.JOB,
        payload=GithubImportJob,
        retry=RETRY,
        concurrency=1,
        on_dead=end_dead_install_plugin,
        max_runtime=MAX_RUNTIME,
    )(run_install_plugin)


register_install_plugin_work(default_registry)
