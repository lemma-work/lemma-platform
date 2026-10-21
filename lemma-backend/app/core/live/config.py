"""Settings for the pod live socket (``/pods/{pod_id}/live``) and its hubs.

Limits are read through ``LiveLimitPort`` rather than straight from here, so a
plan-backed provider can replace them at startup the way it replaces work
limits (``install_live_limit_port``). The batch, wake and poll knobs are shared
by every topic's hub, so a pod's hubs pace themselves alike.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path


class LiveSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    live_max_subscriptions_per_socket: int = Field(
        default=50,
        ge=1,
        description=(
            "Subscriptions one live socket may hold at once. A subscribe past it "
            "is refused with an `error` frame; the socket stays open. Env: "
            "``LIVE_MAX_SUBSCRIPTIONS_PER_SOCKET``."
        ),
    )
    live_max_sockets_per_user_per_pod: int = Field(
        default=5,
        ge=1,
        description=(
            "Live sockets one user may hold open on one pod across every API "
            "process. The next is closed with 4429. Env: "
            "``LIVE_MAX_SOCKETS_PER_USER_PER_POD``."
        ),
    )
    live_limit_overrides_json: str = Field(
        default="",
        description=(
            "Per-pod exceptions to the two limits above, as a JSON list of "
            '``{"pod_id": "<uuid>", "subscriptions_per_socket": n, '
            '"sockets_per_user": n}``. Env: ``LIVE_LIMIT_OVERRIDES_JSON``.'
        ),
    )
    live_max_changes_per_batch: int = Field(
        default=500,
        ge=1,
        description=(
            "Changes for one subscription in one batch above which the server "
            "sends `resync` instead of the rows. Env: "
            "``LIVE_MAX_CHANGES_PER_BATCH``."
        ),
    )
    live_socket_queue_bytes: int = Field(
        default=4 * 1024 * 1024,
        ge=64 * 1024,
        description=(
            "Encoded bytes a socket may have waiting to send. Past it the "
            "queued change frames are dropped and replaced by `resync`. Env: "
            "``LIVE_SOCKET_QUEUE_BYTES``."
        ),
    )
    live_wake_coalesce_ms: int = Field(
        default=50,
        ge=0,
        description=(
            "Wakes for one pod arriving within this window are read as one. "
            "Env: ``LIVE_WAKE_COALESCE_MS``."
        ),
    )
    live_idle_poll_seconds: float = Field(
        default=5.0,
        gt=0,
        description=(
            "A hub with subscribers reads its pod's log at least this often, "
            "so a lost wake delays a change rather than losing it. Env: "
            "``LIVE_IDLE_POLL_SECONDS``."
        ),
    )
    live_authz_check_seconds: float = Field(
        default=2.0,
        gt=0,
        description=(
            "How often a hub compares its pod's authorization epoch; a change "
            "re-checks every subscription's access. Env: "
            "``LIVE_AUTHZ_CHECK_SECONDS``."
        ),
    )
    live_authz_recheck_seconds: float = Field(
        default=60.0,
        gt=0,
        description=(
            "How often a hub re-checks every subscription's access whether or "
            "not the authorization epoch moved: the bound on how long a lost "
            "epoch bump lets a reader keep a subscription it lost the right "
            "to. Env: ``LIVE_AUTHZ_RECHECK_SECONDS``."
        ),
    )
    live_typing_calls_per_minute: int = Field(
        default=30,
        ge=1,
        description=(
            "`typing` calls one socket may make per minute; past it the call is "
            "answered with a `limit` error. Env: ``LIVE_TYPING_CALLS_PER_MINUTE``."
        ),
    )
    live_typing_calls_per_user_per_minute: int = Field(
        default=60,
        ge=1,
        description=(
            "`typing` calls one user may make per minute on a pod, across all "
            "their sockets and every API process (counted in Redis); past it "
            "the call is answered with a `limit` error. Env: "
            "``LIVE_TYPING_CALLS_PER_USER_PER_MINUTE``."
        ),
    )
    live_read_calls_per_minute: int = Field(
        default=120,
        ge=1,
        description=(
            "`read` calls one socket may make per minute. Env: "
            "``LIVE_READ_CALLS_PER_MINUTE``."
        ),
    )
    live_answer_stream_seconds: float = Field(
        default=900.0,
        gt=0,
        description=(
            "Longest an agent's answer is relayed as `line.streaming` deltas "
            "before the relay gives up and the client waits for the line. Env: "
            "``LIVE_ANSWER_STREAM_SECONDS``."
        ),
    )
    live_hub_linger_seconds: float = Field(
        default=10.0,
        ge=0,
        description=(
            "How long a hub with no subscribers stays up, so a reconnecting "
            "page does not tear it down and rebuild it. Env: "
            "``LIVE_HUB_LINGER_SECONDS``."
        ),
    )


live_settings = LiveSettings()
