from enum import Enum


class MemberConnectStanding(str, Enum):
    CONNECTED = "CONNECTED"
    MISSING = "MISSING"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"

    def __str__(self) -> str:
        return str(self.value)
