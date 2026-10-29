from enum import Enum


class StreamBeaconCodecType0(str, Enum):
    H264 = "h264"
    VP8 = "vp8"

    def __str__(self) -> str:
        return str(self.value)
