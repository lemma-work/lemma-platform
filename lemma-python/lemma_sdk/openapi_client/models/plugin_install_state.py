from enum import Enum


class PluginInstallState(str, Enum):
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    INSTALLED = "INSTALLED"
    INSTALLING = "INSTALLING"
    NEEDS_ANSWER = "NEEDS_ANSWER"
    WAITING = "WAITING"

    def __str__(self) -> str:
        return str(self.value)
