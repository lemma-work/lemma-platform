"""How a GitHub import reaches the repository: the pod's `github` connector.

Out of ``handlers.py``, which is past the file-size ceiling, because this is
the one part of the import with an opinion about accounts: none is named. The
archive is fetched through the pod's `github` pod connector as the person who
started the import -- their own account for a per-member connector, the shared
one for a shared connector -- and anonymously when the connector refuses (no
`github` connector in the pod, or no account of theirs on it), which is all a
public repository needs. The connector's refusal is what the person sees when
anonymous access did not reach the repository either (`github_fetcher`).
"""

from __future__ import annotations

from app.core.authorization.scope import uow_scope
from app.core.authorization.service import AuthorizationDataService
from app.modules.pod_bundle.config import pod_bundle_settings
from app.modules.pod_bundle.domain.state import ImportState
from app.modules.pod_bundle.events.work import BundleWorkerContext
from app.modules.pod_bundle.infrastructure import github_fetcher

_ANONYMOUS_WARNING = "GitHub import used anonymous API access and may be rate-limited."
_POD_CONNECTOR_SLUG = "github"


def github_import_fetcher(
    worker_ctx: BundleWorkerContext, state: ImportState
) -> github_fetcher.GithubBundleFetcher:
    return github_fetcher.GithubBundleFetcher(
        operation_runner=_operation_runner(worker_ctx=worker_ctx, state=state)
    )


def note_anonymous_access(fetcher: object, state: ImportState) -> None:
    if (
        getattr(fetcher, "used_anonymous_access", False)
        and pod_bundle_settings.pod_bundle_github_token is None
        and _ANONYMOUS_WARNING not in state.warnings
    ):
        state.warnings.append(_ANONYMOUS_WARNING)


def _operation_runner(*, worker_ctx: BundleWorkerContext, state: ImportState):
    async def run(operation_name: str, payload: dict) -> dict:
        from app.modules.connectors.contracts.provisioning import (
            execute_resolved,
            resolve_operation,
        )

        # Resolve in a short scope, as the person who started the import; call
        # GitHub with no connection held.
        async with uow_scope(worker_ctx.uow_factory) as uow:
            actor = await AuthorizationDataService(uow.session).build_user_context(
                user_id=state.user_id,
                pod_id=state.pod_id,
            )
            resolved = await resolve_operation(
                uow,
                ctx=actor,
                pod_id=state.pod_id,
                connector=_POD_CONNECTOR_SLUG,
                operation_name=operation_name,
                payload=payload,
            )
            # A first per-member call adds the person's account to the pod.
            await uow.commit()
        async with uow_scope(worker_ctx.uow_factory) as uow:
            response = await execute_resolved(uow, resolved)
        if hasattr(response, "model_dump"):
            return response.model_dump(mode="json")
        return response if isinstance(response, dict) else {}

    return run
