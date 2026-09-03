from __future__ import annotations

from uuid import UUID

from ..errors import LemmaNotFoundError
from ..openapi_client.api.schedules import (
    schedule_create,
    schedule_delete,
    schedule_get,
    schedule_list,
    schedule_run_list,
    schedule_run_retry,
    schedule_update,
)
from ..openapi_client.models.create_schedule_request import CreateScheduleRequest
from ..openapi_client.models.schedule_detail_response import ScheduleDetailResponse
from ..openapi_client.models.schedule_list_response import ScheduleListResponse
from ..openapi_client.models.schedule_run_list_response import ScheduleRunListResponse
from ..openapi_client.models.schedule_run_response import ScheduleRunResponse
from ..openapi_client.models.schedule_type import ScheduleType
from ..openapi_client.models.update_schedule_request import UpdateScheduleRequest
from ..openapi_client.types import UNSET
from .base import BoundResource, as_uuid


class PodSchedules(BoundResource):
    def _schedule_id(self, schedule: str) -> UUID:
        """Resolve a schedule id or name to an id.

        The name goes to the server's exact-match filter rather than being
        matched client-side over a listing: a listing is capped, so a pod with
        more schedules than the cap reported "not found" for a schedule that
        exists -- and every name-addressed call paid for a full page first.
        """
        try:
            return as_uuid(schedule)
        except ValueError:
            pass

        page = self.list(name=schedule, limit=2)
        for item in getattr(page, "items", []) or []:
            if getattr(item, "name", None) == schedule:
                return item.id
        raise LemmaNotFoundError(
            status_code=404,
            message=(
                f"No schedule named {schedule!r} in this pod. "
                "Pass a schedule id, or list schedules to see the names."
            ),
        )

    def list(
        self,
        *,
        schedule_type: ScheduleType | str | None = None,
        is_active: bool | None = None,
        agent_name: str | None = None,
        workflow_name: str | None = None,
        name: str | None = None,
        limit: int = 100,
        page_token: str | None = None,
    ) -> ScheduleListResponse:
        if isinstance(schedule_type, str):
            schedule_type = ScheduleType(schedule_type)
        return self._call(
            schedule_list,
            self._pod_uuid(),
            schedule_type=schedule_type if schedule_type is not None else UNSET,
            is_active=is_active if is_active is not None else UNSET,
            agent_name=agent_name if agent_name is not None else UNSET,
            workflow_name=workflow_name if workflow_name is not None else UNSET,
            name=name if name is not None else UNSET,
            limit=limit,
            page_token=page_token if page_token is not None else UNSET,
        )

    def create(self, request: CreateScheduleRequest) -> ScheduleDetailResponse:
        return self._call(schedule_create, self._pod_uuid(), body=request)

    def get(self, schedule_id: str) -> ScheduleDetailResponse:
        return self._call(
            schedule_get, self._pod_uuid(), self._schedule_id(schedule_id)
        )

    def update(
        self, schedule_id: str, request: UpdateScheduleRequest
    ) -> ScheduleDetailResponse:
        return self._call(
            schedule_update,
            self._pod_uuid(),
            self._schedule_id(schedule_id),
            body=request,
        )

    def delete(self, schedule_id: str) -> None:
        self._call(schedule_delete, self._pod_uuid(), self._schedule_id(schedule_id))

    def runs(self, schedule_id: str, *, limit: int = 100) -> ScheduleRunListResponse:
        return self._call(
            schedule_run_list,
            self._pod_uuid(),
            self._schedule_id(schedule_id),
            limit=limit,
        )

    def retry_run(self, schedule_id: str, run_id: str) -> ScheduleRunResponse:
        return self._call(
            schedule_run_retry,
            self._pod_uuid(),
            self._schedule_id(schedule_id),
            as_uuid(run_id),
        )
