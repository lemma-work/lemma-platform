from enum import Enum


class PodOperationExecutionRequestActAs(str, Enum):
    APP = "app"
    USER = "user"

    def __str__(self) -> str:
        return str(self.value)
