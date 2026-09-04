from enum import Enum


class OperationDiscoveryStatus(str, Enum):
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"
    OK = "ok"

    def __str__(self) -> str:
        return str(self.value)
