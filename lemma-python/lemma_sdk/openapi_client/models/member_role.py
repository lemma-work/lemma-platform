from enum import Enum


class MemberRole(str, Enum):
    GUEST = "guest"
    MEMBER = "member"
    OWNER = "owner"

    def __str__(self) -> str:
        return str(self.value)
