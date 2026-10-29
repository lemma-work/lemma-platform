"""`/machine-consents`: which apps a person lets run code on their own machine.

An app acts as its viewer, so without this any page a person opened could run
commands as them. The first time an app asks, it is refused with
``MACHINE_CONSENT_REQUIRED`` and shows a card; the person says yes **in
Lemma**, here -- a consent asked for from the app's own page is refused, or
the app could simply say yes for them. Revoked from Settings.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel
from sqlalchemy import select, update

from app.modules.workspace.api.controllers.machine_dependencies import (
    UowFactoryDep,
    caller_for,
)
from app.modules.workspace.api.controllers.machines_controller import machines_enabled
from app.modules.workspace.domain.machine_access import CallerKind
from app.modules.workspace.domain.machine_errors import (
    MachineNotFound,
    MachinePolicyRefused,
)
from app.modules.workspace.infrastructure.machine_models import AppMachineConsentModel

router = APIRouter(tags=["Machine Consents"], dependencies=[Depends(machines_enabled)])


class GrantConsentRequest(BaseModel):
    app_id: UUID


class ConsentResponse(BaseModel):
    app_id: UUID
    pod_id: UUID
    app_name: str | None = None
    granted_at: datetime


class ConsentListResponse(BaseModel):
    consents: list[ConsentResponse]


async def _person(request: Request, uow) -> UUID:
    caller = await caller_for(request, uow)
    if caller.kind is not CallerKind.USER or caller.user_id is None:
        raise MachinePolicyRefused(
            "Only you can let an app use your machine, from Lemma itself.",
            code="MACHINE_CONSENT_FROM_LEMMA_ONLY",
        )
    return caller.user_id


@router.get(
    "/machine-consents",
    response_model=ConsentListResponse,
    operation_id="machine.consent.list",
    summary="Apps you let run code on your machine",
)
async def list_consents(
    request: Request, uow_factory: UowFactoryDep
) -> ConsentListResponse:
    async with uow_factory() as uow:
        user_id = await _person(request, uow)
        rows = (
            await uow.session.scalars(
                select(AppMachineConsentModel)
                .where(
                    AppMachineConsentModel.user_id == user_id,
                    AppMachineConsentModel.revoked_at.is_(None),
                )
                .order_by(AppMachineConsentModel.created_at.desc())
                .limit(500)
            )
        ).all()
        names = await _app_names(uow, [row.app_id for row in rows])
    return ConsentListResponse(
        consents=[
            ConsentResponse(
                app_id=row.app_id,
                pod_id=row.pod_id,
                app_name=names.get(row.app_id),
                granted_at=row.created_at,
            )
            for row in rows
        ]
    )


async def _app_names(uow, app_ids: list[UUID]) -> dict[UUID, str]:
    from app.modules.apps.contracts.ready_app import app_names_by_id

    return await app_names_by_id(uow=uow, app_ids=app_ids)


@router.post(
    "/machine-consents",
    response_model=ConsentResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="machine.consent.grant",
    summary="Let an app run code on your machine",
)
async def grant_consent(
    body: GrantConsentRequest, request: Request, uow_factory: UowFactoryDep
) -> ConsentResponse:
    async with uow_factory() as uow:
        user_id = await _person(request, uow)
        from app.modules.apps.contracts.ready_app import app_identity

        app = await app_identity(uow=uow, app_id=body.app_id)
        if app is None:
            raise MachineNotFound(f"app {body.app_id}")
        existing = await uow.session.scalar(
            select(AppMachineConsentModel).where(
                AppMachineConsentModel.user_id == user_id,
                AppMachineConsentModel.app_id == app.id,
                AppMachineConsentModel.revoked_at.is_(None),
            )
        )
        if existing is None:
            existing = AppMachineConsentModel(
                user_id=user_id, app_id=app.id, pod_id=app.pod_id
            )
            uow.session.add(existing)
            await uow.session.flush()
        response = ConsentResponse(
            app_id=app.id,
            pod_id=app.pod_id,
            app_name=app.name,
            granted_at=existing.created_at,
        )
        await uow.commit()
    return response


@router.delete(
    "/machine-consents/{app_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="machine.consent.revoke",
    summary="Stop an app running code on your machine",
)
async def revoke_consent(
    app_id: UUID, request: Request, uow_factory: UowFactoryDep
) -> None:
    async with uow_factory() as uow:
        user_id = await _person(request, uow)
        await uow.session.execute(
            update(AppMachineConsentModel)
            .where(
                AppMachineConsentModel.user_id == user_id,
                AppMachineConsentModel.app_id == app_id,
                AppMachineConsentModel.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(timezone.utc))
        )
        await uow.commit()
