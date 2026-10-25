from enum import Enum


class GranteeRefGranteeType(str, Enum):
    POD_MEMBER = "POD_MEMBER"
    ROLE = "ROLE"

    def __str__(self) -> str:
        return str(self.value)
