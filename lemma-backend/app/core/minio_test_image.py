import hashlib
import subprocess
from pathlib import Path
from typing import Protocol


# Source pins live in the Dockerfile; its content also invalidates local images.
_BUILD = Path(__file__).resolve().parents[2] / "test-images" / "minio"
MINIO_IMAGE = (
    "lemma-e2e-minio:"
    + hashlib.sha256((_BUILD / "Dockerfile").read_bytes()).hexdigest()[:16]
)


class DockerCommand(Protocol):
    def __call__(
        self, args: list[str], *, capture_output: bool = False, check: bool = False
    ) -> subprocess.CompletedProcess[bytes]: ...


def ensure_minio_image(run: DockerCommand = subprocess.run) -> None:
    """Build the pinned fixture locally when the CI or Docker cache is cold."""
    existing = run(["docker", "image", "inspect", MINIO_IMAGE], capture_output=True)
    if existing.returncode != 0:
        run(["docker", "build", "--tag", MINIO_IMAGE, str(_BUILD)], check=True)
