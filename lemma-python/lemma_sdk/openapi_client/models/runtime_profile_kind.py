from enum import Enum


class RuntimeProfileKind(str, Enum):
    EXTERNAL_AGENT = "EXTERNAL_AGENT"
    HARNESS = "HARNESS"
    MODEL_PROVIDER = "MODEL_PROVIDER"

    def __str__(self) -> str:
        return str(self.value)
