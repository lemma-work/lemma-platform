from enum import Enum


class ResourceVisibility(str, Enum):
    ORGANIZATION = "ORGANIZATION"
    PERSONAL = "PERSONAL"
    POD = "POD"
    PUBLIC = "PUBLIC"
    RESTRICTED = "RESTRICTED"

    def __str__(self) -> str:
        return str(self.value)
