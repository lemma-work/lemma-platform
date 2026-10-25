from enum import Enum


class PodConnectorVisibility(str, Enum):
    POD = "POD"
    RESTRICTED = "RESTRICTED"

    def __str__(self) -> str:
        return str(self.value)
