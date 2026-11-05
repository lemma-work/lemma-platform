"""Per-class shutdown grace and per-kind time limits: the parts with no database."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import BaseModel

from app.core.infrastructure.work import config as work_config
from app.core.infrastructure.work.config import CLASS_DEFAULTS, class_shutdown_grace
from app.core.infrastructure.work.context import FastContext
from app.core.infrastructure.work.job import JobContext
from app.core.infrastructure.work.registry import WorkRegistry
from app.core.infrastructure.work.types import WorkClass, WorkMode


class _Empty(BaseModel):
    pass


def test_interactive_has_a_longer_grace_than_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(work_config.work_settings, "work_shutdown_grace_json", "")
    monkeypatch.setattr(work_config.work_settings, "work_shutdown_grace_seconds", 20.0)
    assert class_shutdown_grace(WorkClass.INTERACTIVE) == 90
    assert class_shutdown_grace(WorkClass.EVENTS) == 20.0


def test_the_json_override_beats_the_builtin_and_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        work_config.work_settings,
        "work_shutdown_grace_json",
        '{"interactive": 120, "events": 5, "bogus": 1}',
    )
    assert class_shutdown_grace(WorkClass.INTERACTIVE) == 120
    assert class_shutdown_grace(WorkClass.EVENTS) == 5
    assert class_shutdown_grace(WorkClass.ADMIN) == (
        work_config.work_settings.work_shutdown_grace_seconds
    )


def test_the_interactive_cap_leaves_room_for_other_pods() -> None:
    interactive = CLASS_DEFAULTS[WorkClass.INTERACTIVE]
    assert interactive.max_running < interactive.slots


def test_max_runtime_is_refused_on_a_fast_kind() -> None:
    registry = WorkRegistry()

    async def handle(ctx: FastContext, payload: _Empty) -> None:
        return None

    with pytest.raises(ValueError, match="max_runtime is for JOB"):
        registry.handler(  # type: ignore[call-overload]
            kind="demo.fast",
            cls=WorkClass.EVENTS,
            mode=WorkMode.FAST,
            payload=_Empty,
            max_runtime=timedelta(seconds=1),
        )(handle)


def test_max_runtime_must_be_positive_and_is_kept_on_the_spec() -> None:
    registry = WorkRegistry()

    async def handle(ctx: JobContext, payload: _Empty) -> None:
        return None

    with pytest.raises(ValueError, match="must be positive"):
        registry.handler(
            kind="demo.zero",
            cls=WorkClass.INTERACTIVE,
            mode=WorkMode.JOB,
            payload=_Empty,
            max_runtime=timedelta(0),
        )(handle)

    registry.handler(
        kind="demo.turn",
        cls=WorkClass.INTERACTIVE,
        mode=WorkMode.JOB,
        payload=_Empty,
        max_runtime=timedelta(seconds=14_700),
    )(handle)
    assert registry.get("demo.turn").max_runtime == timedelta(seconds=14_700)
