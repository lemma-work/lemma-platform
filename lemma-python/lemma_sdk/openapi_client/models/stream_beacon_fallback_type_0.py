from enum import Enum


class StreamBeaconFallbackType0(str, Enum):
    ERROR = "error"
    ICE_FAILED = "ice-failed"
    ICE_TIMEOUT = "ice-timeout"
    NO_STREAMER = "no-streamer"
    NO_TURN = "no-turn"
    UNSUPPORTED = "unsupported"

    def __str__(self) -> str:
        return str(self.value)
