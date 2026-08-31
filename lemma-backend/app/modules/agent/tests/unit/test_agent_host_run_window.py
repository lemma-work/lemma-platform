"""How long an Agent Host run may live, and who gets to decide.

Three ceilings used to disagree by a factor of four: the worker task drove the
run for 30 minutes, the credential in its START_RUN lasted an hour, and the
deadline advertised to the machine on the user's desk was two hours. The first
to bite decided the outcome, and both of the ones that bit did so silently --
the worker's cancellation left the remote agent running for another 90 minutes,
and the credential's expiry left it running with every Lemma tool 401ing.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid7

import pytest

from app.core.infrastructure.jobs.streaq_runtime import (
    AGENT_RUN_JOB_TIMEOUT_SECONDS,
)
from app.modules.agent.events.handlers import _ORPHANED_RUN_CUTOFF_SECONDS
from app.modules.agent.infrastructure.agent_host.repository_common import (
    DEFAULT_PERMISSION_COMMAND_TTL_SECONDS,
    DEFAULT_RUN_LEASE_SECONDS,
)
from app.modules.agent.infrastructure.harnesses.agent_host.run_window import (
    CREDENTIAL_DEADLINE_MESSAGE,
    CREDENTIAL_REFRESH_MARGIN_SECONDS,
    CREDENTIAL_SAFETY_MARGIN_SECONDS,
    DEADLINE_MESSAGE,
    DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS,
    DispatchedRun,
    credential_bounded_timeout,
    credential_exhausted,
    credential_refresh_due,
)
from app.modules.agent.infrastructure.harnesses.remote_payload import token_expires_at


# The window the host holds an ACP permission request open for, in Rust
# (PERMISSION_DECISION_TIMEOUT in desktop/agent-host/src/runtime.rs). Mirrored here so a
# change on either side breaks this, rather than quietly making every
# mid-run approval unanswerable.
HOST_PERMISSION_WINDOW_SECONDS = 30 * 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TestTheCeilingsAgree:
    def test_the_worker_outlives_the_deadline_we_advertise(self) -> None:
        """Otherwise streaq kills the job while the host is still working, and
        the remote agent keeps running a turn Lemma has reported as failed."""
        assert AGENT_RUN_JOB_TIMEOUT_SECONDS > DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS

    def test_the_worker_has_room_to_cancel_and_finalize(self) -> None:
        headroom = (
            AGENT_RUN_JOB_TIMEOUT_SECONDS - DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS
        )
        assert headroom >= 120

    def test_the_orphan_sweep_never_reaps_a_healthy_run(self) -> None:
        assert _ORPHANED_RUN_CUTOFF_SECONDS > AGENT_RUN_JOB_TIMEOUT_SECONDS

    def test_a_run_may_last_hours(self) -> None:
        """An agent host run is a person's real task, not a request.

        A run doing steady work was killed at minute fifty with "run deadline
        elapsed". The number came from a chain of ceilings whose binding link --
        the one-hour MCP credential -- stopped binding once the harness began
        refreshing it in flight, and the note explaining the number outlived the
        constraint it described.
        """
        assert DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS >= 4 * 60 * 60

    def test_a_host_that_goes_away_is_caught_by_the_lease_not_the_deadline(
        self,
    ) -> None:
        """Why a long deadline is safe.

        The deadline does not detect a host that crashed, slept or lost its
        network -- the lease does, and it is an order of magnitude shorter. If
        that ever stopped being true, a dead host would sit RUNNING for hours.
        """
        assert DEFAULT_RUN_LEASE_SECONDS < 300
        assert DEFAULT_RUN_LEASE_SECONDS * 10 < DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS

    def test_a_permission_parked_mid_run_can_still_be_answered(self) -> None:
        """When the run is shorter than the host's permission window, every
        permission raised past that point is guaranteed to go unanswered."""
        assert DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS > HOST_PERMISSION_WINDOW_SECONDS

    def test_a_queued_decision_outlives_the_window_it_answers(self) -> None:
        """At the default five-minute command TTL, a decision made at minute six
        expired before a sleeping laptop could ever collect it."""
        assert DEFAULT_PERMISSION_COMMAND_TTL_SECONDS >= HOST_PERMISSION_WINDOW_SECONDS


class TestCredentialRefresh:
    """A run should be bounded by its work, not by a one-hour token.

    The credential is renewed in flight now, so the ceiling only bites when
    every renewal failed — at which point ending the run is still far better
    than letting every ``lemma_*`` call 401 in silence for the rest of it.
    """

    def test_a_fresh_credential_needs_no_renewal(self) -> None:
        assert not credential_refresh_due(
            expires_at=_now() + timedelta(hours=1), now=_now()
        )

    def test_renewal_starts_well_before_expiry(self) -> None:
        """With room for several attempts before the safety margin bites."""
        assert credential_refresh_due(
            expires_at=_now()
            + timedelta(seconds=CREDENTIAL_REFRESH_MARGIN_SECONDS - 1),
            now=_now(),
        )
        assert CREDENTIAL_REFRESH_MARGIN_SECONDS > CREDENTIAL_SAFETY_MARGIN_SECONDS

    def test_a_credential_that_was_never_renewed_ends_the_run(self) -> None:
        assert credential_exhausted(
            expires_at=_now() + timedelta(seconds=CREDENTIAL_SAFETY_MARGIN_SECONDS - 1),
            now=_now(),
        )

    def test_a_run_is_never_ended_before_renewal_has_been_tried(self) -> None:
        """Exhaustion must not fire in the same window renewal opens in, or a
        run would be killed at the first refresh check rather than renewed."""
        at_refresh_time = _now() + timedelta(
            seconds=CREDENTIAL_REFRESH_MARGIN_SECONDS - 1
        )
        assert not credential_exhausted(expires_at=at_refresh_time, now=_now())

    def test_an_unreadable_credential_bounds_nothing(self) -> None:
        assert not credential_refresh_due(expires_at=None, now=_now())
        assert not credential_exhausted(expires_at=None, now=_now())


class TestCredentialBoundedTimeout:
    def test_a_fresh_credential_leaves_the_configured_window_alone(self) -> None:
        seconds, bounded = credential_bounded_timeout(
            configured_seconds=DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS,
            credential_expires_at=_now() + timedelta(hours=1),
            now=_now(),
            agent_run_id=uuid7(),
        )

        assert seconds == DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS
        assert bounded is False

    def test_a_short_credential_no_longer_shortens_the_run(self) -> None:
        """The token is renewed in flight, so its expiry is a starting point.

        This used to clamp the window to whatever was left of the credential.
        Harmless while the window was fifty minutes -- a fresh one-hour token
        outlived it, so the clamp never fired -- and wrong against a window
        measured in hours, where it would fire on every run and quietly put the
        old ceiling back.
        """
        seconds, bounded = credential_bounded_timeout(
            configured_seconds=DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS,
            credential_expires_at=_now() + timedelta(minutes=20),
            now=_now(),
            agent_run_id=uuid7(),
        )

        assert bounded is False
        assert seconds == DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS

    def test_a_fresh_credential_does_not_cap_a_multi_hour_window(self) -> None:
        """The regression that raising the window would otherwise have hit: a
        one-hour token against a four-hour run."""
        seconds, bounded = credential_bounded_timeout(
            configured_seconds=DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS,
            credential_expires_at=_now() + timedelta(hours=1),
            now=_now(),
            agent_run_id=uuid7(),
        )

        assert bounded is False
        assert seconds == DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS

    def test_an_almost_expired_credential_refuses_to_dispatch(self) -> None:
        with pytest.raises(RuntimeError, match="expires too soon"):
            credential_bounded_timeout(
                configured_seconds=DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS,
                credential_expires_at=_now() + timedelta(seconds=30),
                now=_now(),
                agent_run_id=uuid7(),
            )

    def test_an_unreadable_credential_falls_back_to_the_configured_window(self) -> None:
        seconds, bounded = credential_bounded_timeout(
            configured_seconds=DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS,
            credential_expires_at=None,
            now=_now(),
            agent_run_id=uuid7(),
        )

        assert (seconds, bounded) == (DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS, False)

    def test_the_failure_says_which_ceiling_ended_the_run(self) -> None:
        """A run cut short by its credential must not be reported as the host
        failing to terminalize; that sends debugging to the wrong machine."""
        capped = DispatchedRun(
            harness_key="codex",
            event_timeout_seconds=600.0,
            credential_bounded=True,
        )
        ordinary = DispatchedRun(
            harness_key="codex",
            event_timeout_seconds=600.0,
            credential_bounded=False,
        )

        assert capped.deadline_message == CREDENTIAL_DEADLINE_MESSAGE
        assert ordinary.deadline_message == DEADLINE_MESSAGE


def _jwt(claims: dict) -> str:
    def segment(payload: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        return raw.rstrip("=")

    return f"{segment({'alg': 'RS256'})}.{segment(claims)}.signature"


class TestTokenExpiryIsPublished:
    def test_the_expiry_round_trips_through_the_mcp_payload(self) -> None:
        expires = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
        from app.modules.agent.infrastructure.harnesses.remote_payload import (
            _token_expiry_iso,
        )

        published = _token_expiry_iso(_jwt({"exp": int(expires.timestamp())}))

        assert token_expires_at({"token_expires_at": published}) == expires

    def test_a_token_with_no_exp_publishes_nothing(self) -> None:
        from app.modules.agent.infrastructure.harnesses.remote_payload import (
            _token_expiry_iso,
        )

        assert _token_expiry_iso(_jwt({"sub": "someone"})) is None

    def test_an_opaque_token_publishes_nothing(self) -> None:
        from app.modules.agent.infrastructure.harnesses.remote_payload import (
            _token_expiry_iso,
        )

        assert _token_expiry_iso("not-a-jwt") is None

    def test_a_payload_without_the_field_reads_as_unbounded(self) -> None:
        assert token_expires_at({}) is None
