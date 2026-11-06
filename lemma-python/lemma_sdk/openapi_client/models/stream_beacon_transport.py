from enum import Enum


class StreamBeaconTransport(str, Enum):
    VNC = "vnc"
    WEBRTC = "webrtc"

    def __str__(self) -> str:
        return str(self.value)
