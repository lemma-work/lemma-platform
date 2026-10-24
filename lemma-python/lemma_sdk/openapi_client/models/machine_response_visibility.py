from enum import Enum


class MachineResponseVisibility(str, Enum):
    POD = "pod"
    PRIVATE = "private"
    RESTRICTED = "restricted"

    def __str__(self) -> str:
        return str(self.value)
