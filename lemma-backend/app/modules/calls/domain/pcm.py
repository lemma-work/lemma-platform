"""16-bit mono PCM arithmetic the engines need: rate conversion and length."""

from __future__ import annotations

import sys
from array import array

_BYTES_PER_SAMPLE = 2


def duration_ms(pcm: bytes, sample_rate: int) -> float:
    return len(pcm) / _BYTES_PER_SAMPLE / sample_rate * 1000


def resample(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
    """Linear-interpolated rate conversion.

    Speech-band audio only, where linear interpolation is inaudible next to the
    codecs on either side of it; a frame is converted on its own, so a seam at
    each frame boundary costs at most one sample's worth of interpolation.
    """
    if source_rate == target_rate or not pcm:
        return pcm
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % _BYTES_PER_SAMPLE])
    if sys.byteorder != "little":
        samples.byteswap()
    count = len(samples)
    out_count = max(1, count * target_rate // source_rate)
    step = source_rate / target_rate
    out = array("h", bytes(out_count * _BYTES_PER_SAMPLE))
    last = count - 1
    for index in range(out_count):
        position = index * step
        left = int(position)
        if left >= last:
            out[index] = samples[last]
            continue
        fraction = position - left
        out[index] = int(samples[left] + (samples[left + 1] - samples[left]) * fraction)
    if sys.byteorder != "little":
        out.byteswap()
    return out.tobytes()
