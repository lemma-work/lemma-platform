from __future__ import annotations

import pytest

from agentbox.profiles import DockerProfileArtifact


def test_runtime_port_is_the_only_required_readiness_probe() -> None:
    artifact = DockerProfileArtifact(
        image="runtime:test",
        command=(),
        readiness_argv=(),
        published_ports=(8090,),
        runtime_port=8090,
    )

    assert artifact.readiness_argv == ()


def test_profile_without_runtime_requires_a_readiness_command() -> None:
    with pytest.raises(ValueError, match="readiness command"):
        DockerProfileArtifact(
            image="batch:test",
            command=("sleep", "infinity"),
            readiness_argv=(),
        )
