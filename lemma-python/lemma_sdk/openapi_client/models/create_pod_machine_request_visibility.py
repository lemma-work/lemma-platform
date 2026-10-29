from enum import Enum


class CreatePodMachineRequestVisibility(str, Enum):
    POD = "pod"
    RESTRICTED = "restricted"

    def __str__(self) -> str:
        return str(self.value)
