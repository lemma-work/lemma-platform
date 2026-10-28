from enum import Enum


class ConnectorPlanStatus(str, Enum):
    CONFLICT = "CONFLICT"
    EXISTS = "EXISTS"
    NEEDS_ACCOUNT = "NEEDS_ACCOUNT"
    NEEDS_INSTALLATION = "NEEDS_INSTALLATION"
    READY = "READY"

    def __str__(self) -> str:
        return str(self.value)
