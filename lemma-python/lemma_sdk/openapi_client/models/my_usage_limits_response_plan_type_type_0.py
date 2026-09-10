from enum import Enum


class MyUsageLimitsResponsePlanTypeType0(str, Enum):
    PERSONAL = "PERSONAL"
    TEAM = "TEAM"

    def __str__(self) -> str:
        return str(self.value)
