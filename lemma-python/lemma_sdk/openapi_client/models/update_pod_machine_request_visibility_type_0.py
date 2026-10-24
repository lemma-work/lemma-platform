from enum import Enum


class UpdatePodMachineRequestVisibilityType0(str, Enum):
    POD = "pod"
    RESTRICTED = "restricted"

    def __str__(self) -> str:
        return str(self.value)
