from enum import Enum


class ProcessResponseState(str, Enum):
    CANCELLED = "cancelled"
    FAILED = "failed"
    RESERVED = "reserved"
    RUNNING = "running"
    STARTING = "starting"
    SUCCEEDED = "succeeded"
    TIMED_OUT = "timed_out"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return str(self.value)
