"""Installing several plugins into one Lemma.

``POST`` queues them and answers ``202`` at once with an ``install_id``; the
server works through them in order whether or not anybody polls. ``GET``
reads where each one is; ``…/retry`` puts the failed ones back in the queue
where they stopped. Each plugin is an ordinary import, readable on its own
under ``/bundle/imports/{import_id}``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.core.api.actor_policy import refuse_named_workloads
from app.core.api.dependencies import CurrentUser
from app.modules.pod_bundle.api.dependencies import (
    InstallUseCasesDep,
    PodEditorDep,
    PodViewerDep,
)
from app.modules.pod_bundle.api.install_schemas import (
    PluginInstallRequest,
    PluginInstallResponse,
)

# As every bundle route: an install runs as the person, not a workload.
router = APIRouter(
    prefix="/pods",
    tags=["Pod Bundle"],
    redirect_slashes=False,
    dependencies=[refuse_named_workloads("export, import or publish a pod")],
)


@router.post(
    "/{pod_id}/bundle/installs",
    response_model=PluginInstallResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="pod.bundle.install.start",
    summary="Install Plugins",
    description=(
        "Install several plugins -- bundles in folders of GitHub repositories -- "
        "into this pod, in the order named. Returns 202 with an install_id at "
        "once; the server fetches, plans and applies each with its install "
        "page's defaults, and keeps going when nobody polls. A plugin whose page "
        "would ask a question stops before anything is applied (NEEDS_ANSWER) "
        "and the next one goes ahead. Each plugin counts as one import against "
        "the daily import limit."
    ),
    dependencies=[PodEditorDep],
)
async def start_install(
    pod_id: UUID,
    data: PluginInstallRequest,
    user: CurrentUser,
    use_cases: InstallUseCasesDep,
) -> PluginInstallResponse:
    install = await use_cases.start_install(
        pod_id=pod_id,
        user_id=user.id,
        sources=[plugin.source() for plugin in data.plugins],
    )
    return PluginInstallResponse.from_install(install)


@router.get(
    "/{pod_id}/bundle/installs/{install_id}",
    response_model=PluginInstallResponse,
    status_code=status.HTTP_200_OK,
    operation_id="pod.bundle.install.get",
    summary="Get Plugin Install Status",
    description=(
        "Where each plugin of an install is: its state, steps done of its "
        "plan, the step running, and why it stopped. Only its starter's: "
        "anyone else gets 404."
    ),
    dependencies=[PodViewerDep],
)
async def get_install(
    pod_id: UUID,
    install_id: UUID,
    user: CurrentUser,
    use_cases: InstallUseCasesDep,
) -> PluginInstallResponse:
    install = await use_cases.get_install(
        pod_id=pod_id, install_id=install_id, user_id=user.id
    )
    return PluginInstallResponse.from_install(install)


@router.post(
    "/{pod_id}/bundle/installs/{install_id}/retry",
    response_model=PluginInstallResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="pod.bundle.install.retry",
    summary="Retry Failed Plugins",
    description=(
        "Put every FAILED plugin of the install back in the queue. One with a "
        "plan carries on from the step that failed; the steps already applied "
        "stay. One that failed before its plan was made starts over. The "
        "others are left as they are."
    ),
    dependencies=[PodEditorDep],
)
async def retry_install(
    pod_id: UUID,
    install_id: UUID,
    user: CurrentUser,
    use_cases: InstallUseCasesDep,
) -> PluginInstallResponse:
    install = await use_cases.retry_install(
        pod_id=pod_id, install_id=install_id, user_id=user.id
    )
    return PluginInstallResponse.from_install(install)
