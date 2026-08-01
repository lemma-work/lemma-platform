"""API schemas for scheduler."""

from __future__ import annotations

from typing import Optional, Dict, Any
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, Field


class ScheduleCronJobRequest(BaseModel):
    """Request schema for scheduling a cron job."""

    schedule_id: UUID = Field(..., description="Schedule ID (also used as job_id)")
    user_id: UUID | None = Field(
        None, description="User who owns the resulting run; optional during rollout"
    )
    cron_expression: str = Field(
        ..., description="Cron expression (e.g., '*/5 * * * *')"
    )
    payload: Optional[Dict[str, Any]] = Field(
        None, description="Optional payload for the event"
    )
    replace_existing: bool = Field(True, description="Replace job if it already exists")


class ScheduleOnceJobRequest(BaseModel):
    """Request schema for scheduling a one-time job."""

    schedule_id: UUID = Field(..., description="Schedule ID (also used as job_id)")
    user_id: UUID | None = Field(
        None, description="User who owns the resulting run; optional during rollout"
    )
    run_date: datetime = Field(
        ..., description="When to run the job (ISO datetime string)"
    )
    payload: Optional[Dict[str, Any]] = Field(
        None, description="Optional payload for the event"
    )
    replace_existing: bool = Field(True, description="Replace job if it already exists")
    logical_schedule: bool = Field(
        False,
        description="Protected marker for a persisted logical one-time schedule",
    )


class JobResponse(BaseModel):
    """Response schema for job information."""

    job_id: str
    schedule_id: UUID
    next_run_time: Optional[datetime] = None
    job_state: Optional[str] = None


class JobListResponse(BaseModel):
    """Response schema for listing jobs."""

    jobs: list[JobResponse]
    total: int


class JobStatusResponse(BaseModel):
    """Response schema for job status."""

    job_id: str
    exists: bool
    next_run_time: Optional[datetime] = None
    job_state: Optional[str] = None
