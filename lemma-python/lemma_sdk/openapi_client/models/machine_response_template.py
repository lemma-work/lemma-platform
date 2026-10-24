from enum import Enum


class MachineResponseTemplate(str, Enum):
    WORKSPACE = "workspace"
    WORKSPACE_DOCKER = "workspace-docker"

    def __str__(self) -> str:
        return str(self.value)
