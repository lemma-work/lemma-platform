from enum import Enum


class SignInRequestStatus(str, Enum):
    DECLINED = "DECLINED"
    PENDING = "PENDING"
    SIGNED_IN = "SIGNED_IN"

    def __str__(self) -> str:
        return str(self.value)
