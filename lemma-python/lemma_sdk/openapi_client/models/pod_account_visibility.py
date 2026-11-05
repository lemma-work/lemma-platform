from enum import Enum


class PodAccountVisibility(str, Enum):
    PERSONAL = "PERSONAL"
    POD = "POD"
    RESTRICTED = "RESTRICTED"

    def __str__(self) -> str:
        return str(self.value)
