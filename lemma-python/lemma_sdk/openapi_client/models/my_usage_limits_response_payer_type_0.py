from enum import Enum


class MyUsageLimitsResponsePayerType0(str, Enum):
    ORGANIZATION = "organization"
    PERSONAL = "personal"

    def __str__(self) -> str:
        return str(self.value)
