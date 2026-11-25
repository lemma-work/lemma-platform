"""Reading and writing web widgets.

Their visitors' sessions and codes are contacts' (``contacts.contracts.visitor_sessions``).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.integrity import violated_constraint
from app.modules.agent_surfaces.domain.web_widgets import (
    WebWidget,
    WidgetAnswer,
    WidgetNameTaken,
)
from app.modules.agent_surfaces.infrastructure.web_widget_models import (
    WebWidgetModel,
)
from app.modules.vault.contracts import SecretScope, legacy_text, vault_for

#: The most widgets one pod lists.
MAX_WIDGETS = 100

#: The index that keeps one widget name to one widget in a pod.
_NAME_TAKEN = "uq_web_widget_pod_name"

SIGNING_SECRET_PURPOSE = "agent_surfaces.web_widget.signing_secret"
_OWNER_TABLE = "agent_surface_web_widgets"


def _scope(pod_id: UUID) -> SecretScope:
    # The pod alone: a widget knows its pod without a lookup.
    return SecretScope(organization_id=None, pod_id=pod_id)


def _widget(row: WebWidgetModel) -> WebWidget:
    return WebWidget(
        id=row.id,
        pod_id=row.pod_id,
        agent_id=row.agent_id,
        name=row.name,
        public_key=row.public_key,
        allowed_origins=tuple(row.allowed_origins or ()),
        answer=WidgetAnswer(row.answer),
        looked_after_by=row.looked_after_by,
        created_at=row.created_at,
        host_issuer=row.host_issuer,
    )


class WebWidgetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- widgets ------------------------------------------------------------

    async def create(
        self,
        *,
        pod_id: UUID,
        agent_id: UUID,
        name: str,
        public_key: str,
        secret: str,
        allowed_origins: list[str],
        answer: WidgetAnswer,
        looked_after_by: UUID | None,
        host_issuer: str,
    ) -> WebWidget:
        """The new widget. Raises ``WidgetNameTaken`` for a name the pod has.

        Left to the unique index rather than looked up first: two requests
        naming the same widget at once both find the name free.
        """
        row = WebWidgetModel(
            pod_id=pod_id,
            agent_id=agent_id,
            name=name,
            public_key=public_key,
            allowed_origins=allowed_origins,
            answer=answer.value,
            looked_after_by=looked_after_by,
            host_issuer=host_issuer,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError as error:
            if violated_constraint(error) == _NAME_TAKEN:
                raise WidgetNameTaken(name) from None
            raise
        # After the savepoint, not in it: a name already taken leaves no secret
        # behind, and the vault's first key load may hand the connection back,
        # which a savepoint cannot survive. Same transaction as the row.
        ref = await vault_for(self.session).put(
            scope=_scope(pod_id),
            purpose=SIGNING_SECRET_PURPOSE,
            value=secret,
            owner_table=_OWNER_TABLE,
        )
        row.signing_secret_id = ref.id
        await self.session.flush()
        return _widget(row)

    async def get(self, *, pod_id: UUID, widget_id: UUID) -> WebWidget | None:
        row = await self.session.scalar(
            select(WebWidgetModel).where(
                WebWidgetModel.id == widget_id, WebWidgetModel.pod_id == pod_id
            )
        )
        return _widget(row) if row else None

    async def by_public_key(self, public_key: str) -> WebWidget | None:
        row = await self.session.scalar(
            select(WebWidgetModel).where(WebWidgetModel.public_key == public_key)
        )
        return _widget(row) if row else None

    async def signing_secret(self, widget_id: UUID) -> str | None:
        found = (
            await self.session.execute(
                select(
                    WebWidgetModel.pod_id,
                    WebWidgetModel.signing_secret_id,
                    WebWidgetModel.signing_secret,
                ).where(WebWidgetModel.id == widget_id)
            )
        ).one_or_none()
        if found is None:
            return None
        pod_id, secret_id, legacy = found
        if secret_id is None:
            # Written by the previous release during the rollout.
            return await legacy_text(legacy)
        revealed = await vault_for(self.session).reveal(
            secret_id, expect=_scope(pod_id), purpose=SIGNING_SECRET_PURPOSE
        )
        return revealed.text()

    async def list(self, *, pod_id: UUID) -> list[WebWidget]:
        rows = await self.session.scalars(
            select(WebWidgetModel)
            .where(WebWidgetModel.pod_id == pod_id)
            .order_by(WebWidgetModel.created_at)
            .limit(MAX_WIDGETS)
        )
        return [_widget(row) for row in rows]

    async def update(
        self, *, pod_id: UUID, widget_id: UUID, values: dict[str, object]
    ) -> WebWidget | None:
        if values:
            await self.session.execute(
                update(WebWidgetModel)
                .where(WebWidgetModel.id == widget_id, WebWidgetModel.pod_id == pod_id)
                .values(**values)
            )
        return await self.get(pod_id=pod_id, widget_id=widget_id)

    async def rotate_secret(self, *, widget_id: UUID, secret: str) -> None:
        """Replace the widget's secret; the old Fernet column is cleared."""
        found = (
            await self.session.execute(
                select(WebWidgetModel.pod_id, WebWidgetModel.signing_secret_id)
                .where(WebWidgetModel.id == widget_id)
                .with_for_update()
            )
        ).one_or_none()
        if found is None:
            return
        pod_id, secret_id = found
        vault = vault_for(self.session)
        if secret_id is not None:
            await vault.replace(
                secret_id,
                expect=_scope(pod_id),
                purpose=SIGNING_SECRET_PURPOSE,
                value=secret,
            )
            values: dict[str, object] = {"signing_secret": None}
        else:
            ref = await vault.put(
                scope=_scope(pod_id),
                purpose=SIGNING_SECRET_PURPOSE,
                value=secret,
                owner_table=_OWNER_TABLE,
            )
            values = {"signing_secret_id": ref.id, "signing_secret": None}
        await self.session.execute(
            update(WebWidgetModel)
            .where(WebWidgetModel.id == widget_id)
            .values(**values)
        )

    async def delete(self, *, pod_id: UUID, widget_id: UUID) -> bool:
        result = await self.session.execute(
            delete(WebWidgetModel).where(
                WebWidgetModel.id == widget_id, WebWidgetModel.pod_id == pod_id
            )
        )
        return bool(result.rowcount)
