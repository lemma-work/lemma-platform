"""Configuration owned by the work runtime, and the per-class defaults behind it."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.infrastructure.work.types import WorkClass
from app.core.log.log import get_logger
from app.core.settings_env import dotenv_path

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ClassDefaults:
    lease: timedelta
    slots: int
    max_running: int
    #: Connections this class's handlers may hold at once: a fast handler's
    #: whole transaction, a job's checkpoints, completion and settling. Fewer
    #: than ``slots`` on purpose -- a job holds none while it does its I/O, and
    #: a fast handler is bounded by its statement timeout.
    db_slots: int
    #: Seconds running handlers get on shutdown before they are cancelled and
    #: released. None = WORK_SHUTDOWN_GRACE_SECONDS.
    shutdown_grace: int | None = None


#: Lease, slots per process, per-namespace cap and DB connections for each
#: class. A job's lease is a heartbeat interval times three, not a bound on how
#: long it may run.
CLASS_DEFAULTS: dict[WorkClass, ClassDefaults] = {
    WorkClass.EVENTS: ClassDefaults(timedelta(seconds=30), 32, 16, 6),
    # Per-pod cap 48, above the automation class's 32. An agent turn holds its
    # slot for the whole turn, including the time it sits idle waiting on an
    # Agent Host -- and an Agent Host runs up to 20 at once by default -- so a
    # pod's cap has to cover its busiest burst of person-driven turns *plus* a
    # host's worth of idle ones, or a pod that uses a host gets its web chats
    # queued behind turns doing nothing. 32 left no room above what the
    # busiest pods already run at once; 48 adds most of a host on top, and
    # still leaves a quarter of one process's slots for every other pod. A pod
    # that needs more gets an override (WORK_LIMIT_OVERRIDES_JSON), not a
    # bigger default for everyone. The grace is long enough for a turn
    # to reach its next step boundary (most model calls end inside it) and
    # release with nothing charged; a turn still mid-call when it runs out is
    # cancelled and resumes from its last committed step under a new lease.
    WorkClass.INTERACTIVE: ClassDefaults(
        timedelta(seconds=60), 64, 48, 3, shutdown_grace=90
    ),
    WorkClass.AUTOMATION: ClassDefaults(timedelta(seconds=60), 64, 32, 3),
    WorkClass.DELIVERY: ClassDefaults(timedelta(seconds=30), 32, 8, 2),
    WorkClass.INGESTION: ClassDefaults(timedelta(seconds=120), 4, 4, 2),
    WorkClass.ADMIN: ClassDefaults(timedelta(seconds=60), 4, 1, 1),
    # Perpetual pollers, one per credential, serialised by a concurrency key
    # rather than a namespace cap.
    WorkClass.RECEIVERS: ClassDefaults(timedelta(seconds=60), 16, 64, 1),
}


class WorkSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    work_classes: str = Field(
        default="",
        description=(
            "Comma-separated work classes this process dispatches. Empty (the "
            "default) runs every class, so a single-process deployment needs "
            "nothing set; 'none' runs no dispatcher at all. A split deployment "
            "sets WORK_CLASSES=ingestion on the memory-heavy worker and the "
            "rest on the other."
        ),
    )
    work_slots_json: str = Field(
        default="",
        description=(
            "Per-class slots per process, overriding the built-in defaults, e.g. "
            '{"events": 64}. Cluster capacity is replicas times slots.'
        ),
    )
    work_max_running_json: str = Field(
        default="",
        description=(
            "Per-class default cap on running items per namespace (pod), "
            'overriding the built-in defaults, e.g. {"interactive": 16}. '
            "Per-pod and per-org exceptions go in WORK_LIMIT_OVERRIDES_JSON."
        ),
    )
    work_limit_overrides_json: str = Field(
        default="",
        description=(
            "JSON list of per-namespace limit overrides. Each entry names one of "
            "'ns', 'pod_id', 'org_id', 'slug' or 'slug_prefix' and carries "
            '"max_running": {"<class>": n} and/or "rates": {"<name>": '
            '{"limit": n, "per_seconds": s}}, e.g. [{"pod_id": "<uuid>", '
            '"max_running": {"interactive": 4}}]. The most specific match '
            "wins: ns/pod_id, then org_id, then slug, then the longest "
            "slug_prefix. A pod's organization and slug come from the pod "
            "module, cached in Redis."
        ),
    )
    work_rates_json: str = Field(
        default="",
        description=(
            "Deployment-wide rate limits by name, overriding the built-in "
            'defaults, e.g. {"llm_filter": {"limit": 30, "per_seconds": 3600}}. '
            "A handler declares which named rates its items draw from; an item "
            "over a rate waits, it is never charged or dropped. Per-pod and "
            'per-org exceptions go in WORK_LIMIT_OVERRIDES_JSON under "rates".'
        ),
    )
    work_db_slots_json: str = Field(
        default="",
        description=(
            "Per-class cap on database connections the class's handlers hold "
            'at once, overriding the built-in defaults, e.g. {"events": 8}. '
            "The work runtime opens its own connection pool sized to the sum "
            "of these for the classes this process runs, plus one per class "
            "for claiming and WORK_DB_CONTROL_SLOTS, so it can never wait on a "
            "connection something else holds."
        ),
    )
    work_db_control_slots: int = Field(
        default=4,
        ge=1,
        description=(
            "Connections reserved for lease heartbeats, gauges, cap refresh and "
            "shutdown release, so none of them queues behind handlers."
        ),
    )
    work_quantum: int = Field(
        default=2,
        ge=1,
        description=(
            "Items one namespace may take per claim round when several are "
            "ready. Larger favours throughput for a busy pod; smaller favours "
            "latency for everyone else."
        ),
    )
    work_poll_interval_seconds: float = Field(
        default=1.0,
        gt=0,
        description=(
            "Longest an idle dispatcher waits before looking again. NOTIFY "
            "usually wakes it sooner; this poll is what still delivers when a "
            "notification is lost, and what notices delayed items coming due."
        ),
    )
    work_listen_enabled: bool = Field(
        default=True,
        description=(
            "Wake dispatchers with LISTEN work_<class> on a dedicated "
            "connection. Off falls back to the poll interval alone."
        ),
    )
    work_fast_statement_timeout_ms: int = Field(
        default=2000,
        ge=1,
        description="statement_timeout applied inside every fast handler's transaction.",
    )
    work_shutdown_grace_seconds: float = Field(
        default=20.0,
        ge=0,
        description=(
            "On shutdown, how long running handlers get to finish or reach a "
            "safe point before they are cancelled, for classes with no grace "
            "of their own (interactive has 90 s built in). Whatever is still "
            "owned afterwards is released back to the queue with nothing "
            "charged."
        ),
    )
    work_shutdown_grace_json: str = Field(
        default="",
        description=(
            "Per-class shutdown grace in whole seconds, overriding both the "
            "built-in class graces and WORK_SHUTDOWN_GRACE_SECONDS, e.g. "
            '{"interactive": 120}. The pod\'s termination grace must exceed '
            "the longest of them, or the platform kills what is still draining."
        ),
    )
    work_gauge_interval_seconds: float = Field(
        default=15.0,
        gt=0,
        description="How often the queue-depth and oldest-ready gauges are sampled.",
    )
    work_cap_refresh_seconds: float = Field(
        default=60.0,
        gt=0,
        description=(
            "How often each dispatcher re-reads per-namespace caps for "
            "namespaces with queued work, so an override change takes effect "
            "without a restart."
        ),
    )
    work_dead_retention_days: int = Field(
        default=14,
        ge=1,
        description="Days a dead item is kept for inspection and retry before deletion.",
    )


work_settings = WorkSettings()

_CLASS_NAMES = frozenset(c.value for c in WorkClass)


@lru_cache(maxsize=8)
def _class_int_overrides(raw: str, setting: str) -> tuple[tuple[WorkClass, int], ...]:
    """Parse ``{"<class>": n}``; malformed input warns and changes nothing."""
    if not raw.strip():
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("work.config.unparseable", setting=setting)
        return ()
    if not isinstance(parsed, dict):
        logger.warning("work.config.unparseable", setting=setting)
        return ()
    result: list[tuple[WorkClass, int]] = []
    for name, value in parsed.items():
        if name in _CLASS_NAMES and isinstance(value, int) and value > 0:
            result.append((WorkClass(name), value))
        else:
            logger.warning(
                "work.config.entry_ignored", setting=setting, entry=str(name)
            )
    return tuple(result)


def class_slots(cls: WorkClass) -> int:
    overrides = dict(
        _class_int_overrides(work_settings.work_slots_json, "WORK_SLOTS_JSON")
    )
    return overrides.get(cls, CLASS_DEFAULTS[cls].slots)


def class_db_slots(cls: WorkClass) -> int:
    overrides = dict(
        _class_int_overrides(work_settings.work_db_slots_json, "WORK_DB_SLOTS_JSON")
    )
    return overrides.get(cls, CLASS_DEFAULTS[cls].db_slots)


def class_shutdown_grace(cls: WorkClass) -> float:
    """Seconds this class's running handlers get on shutdown."""
    overrides = dict(
        _class_int_overrides(
            work_settings.work_shutdown_grace_json, "WORK_SHUTDOWN_GRACE_JSON"
        )
    )
    if cls in overrides:
        return float(overrides[cls])
    builtin = CLASS_DEFAULTS[cls].shutdown_grace
    if builtin is not None:
        return float(builtin)
    return work_settings.work_shutdown_grace_seconds


def default_max_running(cls: WorkClass) -> int:
    overrides = dict(
        _class_int_overrides(
            work_settings.work_max_running_json, "WORK_MAX_RUNNING_JSON"
        )
    )
    return overrides.get(cls, CLASS_DEFAULTS[cls].max_running)


def enabled_classes(raw: str | None = None) -> list[WorkClass]:
    """Classes this process dispatches, from ``WORK_CLASSES``. Unknown names refuse to start."""
    value = (work_settings.work_classes if raw is None else raw).strip().lower()
    if not value:
        return list(WorkClass)
    if value == "none":
        return []
    selected: list[WorkClass] = []
    for part in (piece.strip() for piece in value.split(",")):
        if not part:
            continue
        try:
            cls = WorkClass(part)
        except ValueError:
            raise ValueError(
                f"WORK_CLASSES contains unknown class {part!r}; valid classes are "
                f"{', '.join(c.value for c in WorkClass)}"
            ) from None
        if cls not in selected:
            selected.append(cls)
    return selected
