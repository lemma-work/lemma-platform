from enum import Enum


class AgentHostIntegrationHealth(str, Enum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    CONFIG_INVALID = "CONFIG_INVALID"
    DISABLED = "DISABLED"
    INSTALLING = "INSTALLING"
    PROBE_FAILED = "PROBE_FAILED"
    READY = "READY"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"

    def __str__(self) -> str:
        return str(self.value)
