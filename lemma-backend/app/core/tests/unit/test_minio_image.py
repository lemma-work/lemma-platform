import subprocess
from dataclasses import dataclass, field

import pytest

from app.core.test_utils import MINIO_IMAGE, ensure_minio_image


@dataclass
class DockerFixture:
    image_exists: bool = False
    build_fails: bool = False
    builds: list[list[str]] = field(default_factory=list)

    def __call__(
        self, args: list[str], *, capture_output: bool = False, check: bool = False
    ) -> subprocess.CompletedProcess[bytes]:
        if args[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(args, 0 if self.image_exists else 1)
        assert args[:2] == ["docker", "build"]
        assert check
        self.builds.append(args)
        if self.build_fails:
            raise subprocess.CalledProcessError(1, args)
        self.image_exists = True
        return subprocess.CompletedProcess(args, 0)


def test_cached_minio_image_does_not_need_network_or_a_build() -> None:
    docker = DockerFixture(image_exists=True)
    ensure_minio_image(docker)
    assert docker.builds == []


def test_missing_minio_image_builds_once_then_reuses_the_fixture() -> None:
    docker = DockerFixture()
    ensure_minio_image(docker)
    ensure_minio_image(docker)
    assert len(docker.builds) == 1
    assert docker.builds[0][:4] == ["docker", "build", "--tag", MINIO_IMAGE]
    assert docker.builds[0][4].endswith("/test-images/minio")


def test_failed_minio_build_stops_before_any_container_can_start() -> None:
    docker = DockerFixture(build_fails=True)
    with pytest.raises(subprocess.CalledProcessError):
        ensure_minio_image(docker)
    assert not docker.image_exists
