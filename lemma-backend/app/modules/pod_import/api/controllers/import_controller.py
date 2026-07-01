"""Pod-import HTTP endpoints.

The three verbs both renderers (CLI, web wizard) drive:
  POST   /pods/{pod_id}/imports            -> plan a bundle, return PLANNED
  GET    /pods/{pod_id}/imports/{id}       -> poll status + per-step progress
  POST   /pods/{pod_id}/imports/{id}/apply -> apply, or resume a FAILED import
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import PodContextDep
from app.modules.pod.api.dependencies import PodServiceDep
from app.modules.pod.domain.pod_entities import PodConfig, PodEntity, PodSource
from app.modules.pod_import.api.dependencies import ImportAppServiceDep
from app.modules.pod_import.api.schemas import ApplyImportRequest, PodImportResponse
from app.modules.pod_import.infrastructure.exporter import BundleExporter
from app.modules.pod_import.infrastructure.staging import peek_pod_manifest

router = APIRouter(prefix="/pods/{pod_id}/imports", tags=["imports"])

# "Create a new pod" path lives at /imports (no pod yet); "install into this pod"
# is the pod-scoped router above.
new_pod_import_router = APIRouter(prefix="/imports", tags=["imports"])


def _bundle_stem(filename: str | None) -> str | None:
    if not filename:
        return None
    stem = filename.rsplit("/", 1)[-1]
    for ext in (".zip", ".tar.gz", ".tgz", ".tar"):
        if stem.lower().endswith(ext):
            return stem[: -len(ext)]
    return stem


async def _unique_pod_name(pod_service, organization_id: UUID, user_id: UUID, desired: str) -> str:
    """A pod name that won't collide in the org. Pod names are unique per org, so
    importing a pod you already have (or a same-named bundle) gets a "(copy)"
    suffix instead of failing with a conflict."""
    pods, _ = await pod_service.list_pods_by_organization(organization_id, user_id, limit=1000)
    taken = {p.name for p in pods}
    if desired not in taken:
        return desired
    for i in range(1, 100):
        candidate = f"{desired} (copy)" if i == 1 else f"{desired} (copy {i})"
        if candidate not in taken:
            return candidate
    return f"{desired} ({user_id.hex[:6]})"


@new_pod_import_router.post(
    "", response_model=PodImportResponse, status_code=status.HTTP_201_CREATED
)
async def import_into_new_pod(
    user: CurrentUser,
    pod_service: PodServiceDep,
    service: ImportAppServiceDep,
    uow: UoWDep,
    organization_id: UUID = Form(...),
    bundle: UploadFile = File(...),
    source_kind: str = Form("upload"),
    source_ref: str | None = Form(None),
) -> PodImportResponse:
    """Create a fresh pod from a bundle, then plan the import into it — the
    "create a new pod" path, a pod the importer fully owns. Where the bundle came
    from (``source_kind``/``source_ref``) is stamped on the pod for provenance."""
    archive = await bundle.read()
    manifest = peek_pod_manifest(archive, bundle.filename)
    desired = str(manifest.get("name") or _bundle_stem(bundle.filename) or "Imported pod")
    name = await _unique_pod_name(pod_service, organization_id, user.id, desired)
    pod = await pod_service.create_pod(
        PodEntity(
            user_id=user.id,
            organization_id=organization_id,
            name=name,
            description=manifest.get("description"),
            icon_url=manifest.get("icon") or manifest.get("icon_url"),
            config=PodConfig(
                source=PodSource(kind=source_kind, ref=source_ref or bundle.filename)
            ),
        ),
        user.id,
    )
    entity = await service.create(
        pod_id=pod.id,
        user_id=user.id,
        archive=archive,
        filename=bundle.filename,
        source_name=source_ref or bundle.filename,
    )
    async with uow:
        await uow.commit()
    return PodImportResponse.from_entity(entity)


@new_pod_import_router.post(
    "/from-pod/{pod_id}", response_model=PodImportResponse, status_code=status.HTTP_201_CREATED
)
async def import_from_pod(
    pod_id: UUID,
    user: CurrentUser,
    pod_service: PodServiceDep,
    service: ImportAppServiceDep,
    uow: UoWDep,
    ctx: PodContextDep,
) -> PodImportResponse:
    """Create a new pod from an existing pod's bundle — the engine behind a
    shared ``/import/p/<id>`` link. The caller must be able to read the source
    pod (org-scoped for now; public/token sharing is a follow-up)."""
    source = await pod_service.get_pod(pod_id, user.id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pod not found")
    pod_name, archive = await BundleExporter(uow).export(
        pod_id=pod_id, user_id=user.id, ctx=ctx, with_data=True
    )
    name = await _unique_pod_name(
        pod_service, source.organization_id, user.id, pod_name or source.name or "Imported pod"
    )
    pod = await pod_service.create_pod(
        PodEntity(
            user_id=user.id,
            organization_id=source.organization_id,
            name=name,
            description=source.description,
            icon_url=source.icon_url,
            config=PodConfig(source=PodSource(kind="link", ref=str(pod_id))),
        ),
        user.id,
    )
    entity = await service.create(
        pod_id=pod.id,
        user_id=user.id,
        archive=archive,
        filename=f"{pod_name or 'pod'}.zip",
        source_name=pod_name,
    )
    async with uow:
        await uow.commit()
    return PodImportResponse.from_entity(entity)


@router.post("", response_model=PodImportResponse, status_code=status.HTTP_201_CREATED)
async def create_import(
    pod_id: UUID,
    user: CurrentUser,
    service: ImportAppServiceDep,
    uow: UoWDep,
    ctx: PodContextDep,
    bundle: UploadFile = File(...),
    source_name: str | None = Form(None),
) -> PodImportResponse:
    """Upload a bundle archive (.zip/.tar.gz); returns the computed plan
    (PLANNED) with requirements + capabilities. Nothing is applied yet."""
    archive = await bundle.read()
    entity = await service.create(
        pod_id=pod_id,
        user_id=user.id,
        archive=archive,
        filename=bundle.filename,
        source_name=source_name,
    )
    async with uow:
        await uow.commit()
    return PodImportResponse.from_entity(entity)


@router.get("/{import_id}", response_model=PodImportResponse)
async def get_import(
    pod_id: UUID,
    import_id: UUID,
    service: ImportAppServiceDep,
    ctx: PodContextDep,
) -> PodImportResponse:
    entity = await service.get(import_id)
    if entity is None or entity.pod_id != pod_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found")
    return PodImportResponse.from_entity(entity)


@router.post("/{import_id}/apply", response_model=PodImportResponse)
async def apply_import(
    pod_id: UUID,
    import_id: UUID,
    service: ImportAppServiceDep,
    uow: UoWDep,
    ctx: PodContextDep,
    body: ApplyImportRequest | None = None,
) -> PodImportResponse:
    """Apply the import, or resume a previously failed one. Re-callable: already
    completed steps are skipped. Reads the bundle staged at create time.
    ``variables`` resolves the bundle's ${var} placeholders (connector accounts;
    pod-member assignees default to the importing user)."""
    entity = await service.apply(
        import_id=import_id, ctx=ctx, variables=(body.variables if body else None)
    )
    if entity is None or entity.pod_id != pod_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found")
    async with uow:
        await uow.commit()
    return PodImportResponse.from_entity(entity)
