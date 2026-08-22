"""Schedule module configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path

# How far back ``consecutive_terminal_failures`` reads when measuring a failure
# streak. Forty times the default breaker threshold of five, which leaves room
# for the statuses the streak skips over without letting the scan grow with the
# ledger. ``schedule_max_consecutive_failures`` is bounded by it below: a
# threshold deeper than the scan is a breaker that can never trip, and nothing
# would have said so.
BREAKER_STREAK_SCAN_LIMIT = 200


class ScheduleSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    schedule_max_consecutive_failures: int = Field(
        default=5,
        le=BREAKER_STREAK_SCAN_LIMIT,
        description=(
            "Deactivate a schedule after this many consecutive execution errors. "
            "Zero or less disables the breaker. Capped at "
            "``BREAKER_STREAK_SCAN_LIMIT`` because the streak is counted from a "
            "bounded scan of the newest runs: a threshold past that depth can "
            "never be reached, so the breaker would silently stop existing. "
            "Refusing to start beats discovering it from a schedule that "
            "retried forever."
        ),
    )
    schedule_run_reinspect_after_minutes: int = Field(
        default=60,
        ge=1,
        description=(
            "How long before the recovery sweep looks at an in-flight run it has "
            "already inspected. Sets the worst-case delay on noticing a *lost* "
            "outcome event, so it trades detection latency against wasted target "
            "reads. Note the ceiling it competes with: the sweep reads 100 rows "
            "every 5 minutes, so with the 1,375 rows production parks on human "
            "form waits, a full round trip already takes ~69 minutes whatever "
            "this is set to. Below that it stops the sweep hot-looping on a "
            "small in-flight set; it cannot make detection faster than the "
            "backlog allows. Env: ``SCHEDULE_RUN_REINSPECT_AFTER_MINUTES``."
        ),
    )
    schedule_minimum_interval_minutes: int = Field(
        default=15,
        ge=1,
        description="Minimum interval between recurring TIME schedule executions.",
    )
    schedule_run_retention_days: int = Field(
        default=90,
        ge=1,
        description=(
            "Delete schedule runs that reached a terminal outcome more than this "
            "many days ago. The ledger is append-only and had no retention at "
            "all: 81k rows growing ~1,000 a day, which every index on the table "
            "pays for. Must stay comfortably longer than a live failure streak "
            "could span, or pruning could shorten a streak the breaker is still "
            "counting. Env: ``SCHEDULE_RUN_RETENTION_DAYS``."
        ),
    )
    schedule_run_retention_batch_size: int = Field(
        default=1000,
        ge=1,
        description=(
            "Rows deleted per transaction by the retention sweep. Env: "
            "``SCHEDULE_RUN_RETENTION_BATCH_SIZE``."
        ),
    )
    schedule_run_retention_budget_seconds: float = Field(
        default=30.0,
        ge=0,
        description=(
            "Wall-clock ceiling for one retention sweep, checked between "
            "batches so a run stops at a batch boundary and the next tick "
            "resumes. Zero disables the drain loop and deletes one batch per "
            "run. Env: ``SCHEDULE_RUN_RETENTION_BUDGET_SECONDS``."
        ),
    )


schedule_settings = ScheduleSettings()
