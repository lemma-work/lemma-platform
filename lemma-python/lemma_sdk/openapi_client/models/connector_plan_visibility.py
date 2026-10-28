from enum import Enum


class ConnectorPlanVisibility(str, Enum):
    POD = "POD"
    RESTRICTED = "RESTRICTED"

    def __str__(self) -> str:
        return str(self.value)
