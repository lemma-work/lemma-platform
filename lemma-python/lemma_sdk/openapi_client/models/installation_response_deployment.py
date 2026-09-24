from enum import Enum


class InstallationResponseDeployment(str, Enum):
    DESKTOP = "desktop"
    SERVER = "server"

    def __str__(self) -> str:
        return str(self.value)
