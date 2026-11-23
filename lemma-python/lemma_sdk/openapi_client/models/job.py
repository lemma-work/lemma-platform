from enum import Enum


class Job(str, Enum):
    ENGINEERING = "ENGINEERING"
    FOUNDER = "FOUNDER"
    HR = "HR"
    MARKETING = "MARKETING"
    OPERATIONS = "OPERATIONS"
    PRODUCT = "PRODUCT"
    PROJECT_MANAGEMENT = "PROJECT_MANAGEMENT"
    SALES = "SALES"
    SUPPORT = "SUPPORT"

    def __str__(self) -> str:
        return str(self.value)
