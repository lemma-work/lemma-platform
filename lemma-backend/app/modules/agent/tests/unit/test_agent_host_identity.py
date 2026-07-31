from datetime import datetime, timedelta, timezone

import pytest

from app.modules.agent.domain.agent_host import (
    AGENT_HOST_OFFLINE_AFTER_SECONDS,
    AGENT_HOST_PROTOCOL_VERSION,
    AgentHostCapacity,
    AgentHostHarnessCapabilities,
    AgentHostHarnessSnapshot,
    AgentHostStatus,
    HostHello,
    effective_agent_host_status,
)
from app.modules.agent.services.agent_host_auth import (
    generate_host_secret,
    generate_pairing_code,
    host_secret_hash,
    pairing_code_hash,
)


def _now() -> datetime:
    return datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


class TestCredentials:
    def test_secrets_are_high_entropy_and_unique(self) -> None:
        secrets = {generate_host_secret() for _ in range(64)}
        assert len(secrets) == 64
        # token_urlsafe(32) is 256 bits, which is what makes an unsalted
        # hash acceptable for this credential.
        assert all(len(secret) >= 40 for secret in secrets)

    def test_pairing_codes_are_unique(self) -> None:
        assert len({generate_pairing_code() for _ in range(64)}) == 64

    def test_hashes_are_stable_and_never_the_plaintext(self) -> None:
        secret = generate_host_secret()
        digest = host_secret_hash(secret)
        assert digest == host_secret_hash(secret)
        assert digest != secret
        assert len(digest) == 64

        code = generate_pairing_code()
        assert pairing_code_hash(code) == pairing_code_hash(code)
        assert pairing_code_hash(code) != code

    def test_distinct_secrets_hash_differently(self) -> None:
        assert host_secret_hash("alpha") != host_secret_hash("beta")


class TestEffectiveStatus:
    def test_fresh_heartbeat_is_online(self) -> None:
        assert (
            effective_agent_host_status(
                AgentHostStatus.ONLINE, _now() - timedelta(seconds=5), now=_now()
            )
            is AgentHostStatus.ONLINE
        )

    def test_stale_heartbeat_falls_back_to_offline(self) -> None:
        stale = _now() - timedelta(seconds=AGENT_HOST_OFFLINE_AFTER_SECONDS + 1)
        assert (
            effective_agent_host_status(AgentHostStatus.ONLINE, stale, now=_now())
            is AgentHostStatus.OFFLINE
        )

    def test_never_seen_is_offline(self) -> None:
        assert (
            effective_agent_host_status(AgentHostStatus.ONLINE, None, now=_now())
            is AgentHostStatus.OFFLINE
        )

    @pytest.mark.parametrize(
        "persisted",
        [AgentHostStatus.REVOKED, AgentHostStatus.UPGRADE_REQUIRED],
    )
    def test_terminal_states_survive_a_fresh_heartbeat(
        self, persisted: AgentHostStatus
    ) -> None:
        """A revoked host must not read as online just because it polled."""
        assert (
            effective_agent_host_status(
                persisted, _now() - timedelta(seconds=1), now=_now()
            )
            is persisted
        )

    def test_draining_is_preserved_while_fresh(self) -> None:
        assert (
            effective_agent_host_status(
                AgentHostStatus.DRAINING, _now() - timedelta(seconds=1), now=_now()
            )
            is AgentHostStatus.DRAINING
        )


class TestProtocolNegotiation:
    def test_matching_version_negotiates(self) -> None:
        hello = HostHello(
            installation_id="install-1",
            host_release="1.0.0",
            protocol_version=AGENT_HOST_PROTOCOL_VERSION,
        )
        assert hello.negotiate() == AGENT_HOST_PROTOCOL_VERSION

    @pytest.mark.parametrize("version", [1, AGENT_HOST_PROTOCOL_VERSION + 1])
    def test_mismatched_version_is_rejected(self, version: int) -> None:
        hello = HostHello(
            installation_id="install-1",
            host_release="1.0.0",
            protocol_version=version,
        )
        with pytest.raises(ValueError):
            hello.negotiate()


class TestCapacity:
    def test_active_cannot_exceed_max(self) -> None:
        with pytest.raises(ValueError):
            AgentHostCapacity(max_runs=1, active_runs=2, available_runs=0)

    def test_available_cannot_exceed_max(self) -> None:
        with pytest.raises(ValueError):
            AgentHostCapacity(max_runs=1, active_runs=0, available_runs=2)

    def test_draining_shape_is_valid(self) -> None:
        capacity = AgentHostCapacity(max_runs=4, active_runs=1, available_runs=0)
        assert capacity.available_runs == 0


class TestHarnessSnapshot:
    def test_harness_key_is_normalized(self) -> None:
        snapshot = AgentHostHarnessSnapshot(
            harness_key="  Claude_Code  ",
            display_name="Claude Code",
            adapter_version="1.0.0",
            health="READY",
            config_revision="rev-1",
            stale_after=_now(),
        )
        assert snapshot.harness_key == "claude-code"

    def test_blank_harness_key_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            AgentHostHarnessSnapshot(
                harness_key="   ",
                display_name="x",
                adapter_version="1.0.0",
                health="READY",
                config_revision="rev-1",
                stale_after=_now(),
            )

    def test_unknown_capabilities_are_preserved_untyped(self) -> None:
        """Only `images` is typed; anything else rides along in the blob.

        This is what lets the host evolve its capability set without the
        server growing fields no code reads.
        """
        capabilities = AgentHostHarnessCapabilities.model_validate(
            {"images": True, "some_future_flag": True}
        )
        assert capabilities.images is True
        dumped = capabilities.model_dump(mode="json")
        assert dumped["some_future_flag"] is True

    def test_capabilities_default_to_no_images(self) -> None:
        assert AgentHostHarnessCapabilities().images is False
