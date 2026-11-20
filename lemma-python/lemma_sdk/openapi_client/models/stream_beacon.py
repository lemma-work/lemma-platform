from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.stream_beacon_codec_type_0 import StreamBeaconCodecType0
from ..models.stream_beacon_fallback_type_0 import StreamBeaconFallbackType0
from ..models.stream_beacon_relay_type_0 import StreamBeaconRelayType0
from ..models.stream_beacon_transport import StreamBeaconTransport
from ..types import UNSET, Unset

T = TypeVar("T", bound="StreamBeacon")


@_attrs_define
class StreamBeacon:
    """What one viewing session got, from the viewer's side.

    Bounded everywhere: it comes from a browser and ends up in a log line.

        Attributes:
            seconds (float):
            transport (StreamBeaconTransport):
            codec (None | StreamBeaconCodecType0 | Unset):
            density (int | None | Unset):
            fallback (None | StreamBeaconFallbackType0 | Unset):
            first_frame_ms (float | None | Unset):
            fps (float | None | Unset):
            kbps (float | None | Unset):
            latency_ms (float | None | Unset):
            relay (None | StreamBeaconRelayType0 | Unset):
    """

    seconds: float
    transport: StreamBeaconTransport
    codec: None | StreamBeaconCodecType0 | Unset = UNSET
    density: int | None | Unset = UNSET
    fallback: None | StreamBeaconFallbackType0 | Unset = UNSET
    first_frame_ms: float | None | Unset = UNSET
    fps: float | None | Unset = UNSET
    kbps: float | None | Unset = UNSET
    latency_ms: float | None | Unset = UNSET
    relay: None | StreamBeaconRelayType0 | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        seconds = self.seconds

        transport = self.transport.value

        codec: None | str | Unset
        if isinstance(self.codec, Unset):
            codec = UNSET
        elif isinstance(self.codec, StreamBeaconCodecType0):
            codec = self.codec.value
        else:
            codec = self.codec

        density: int | None | Unset
        if isinstance(self.density, Unset):
            density = UNSET
        else:
            density = self.density

        fallback: None | str | Unset
        if isinstance(self.fallback, Unset):
            fallback = UNSET
        elif isinstance(self.fallback, StreamBeaconFallbackType0):
            fallback = self.fallback.value
        else:
            fallback = self.fallback

        first_frame_ms: float | None | Unset
        if isinstance(self.first_frame_ms, Unset):
            first_frame_ms = UNSET
        else:
            first_frame_ms = self.first_frame_ms

        fps: float | None | Unset
        if isinstance(self.fps, Unset):
            fps = UNSET
        else:
            fps = self.fps

        kbps: float | None | Unset
        if isinstance(self.kbps, Unset):
            kbps = UNSET
        else:
            kbps = self.kbps

        latency_ms: float | None | Unset
        if isinstance(self.latency_ms, Unset):
            latency_ms = UNSET
        else:
            latency_ms = self.latency_ms

        relay: None | str | Unset
        if isinstance(self.relay, Unset):
            relay = UNSET
        elif isinstance(self.relay, StreamBeaconRelayType0):
            relay = self.relay.value
        else:
            relay = self.relay

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "seconds": seconds,
                "transport": transport,
            }
        )
        if codec is not UNSET:
            field_dict["codec"] = codec
        if density is not UNSET:
            field_dict["density"] = density
        if fallback is not UNSET:
            field_dict["fallback"] = fallback
        if first_frame_ms is not UNSET:
            field_dict["first_frame_ms"] = first_frame_ms
        if fps is not UNSET:
            field_dict["fps"] = fps
        if kbps is not UNSET:
            field_dict["kbps"] = kbps
        if latency_ms is not UNSET:
            field_dict["latency_ms"] = latency_ms
        if relay is not UNSET:
            field_dict["relay"] = relay

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        seconds = d.pop("seconds")

        transport = StreamBeaconTransport(d.pop("transport"))

        def _parse_codec(data: object) -> None | StreamBeaconCodecType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                codec_type_0 = StreamBeaconCodecType0(data)

                return codec_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | StreamBeaconCodecType0 | Unset, data)

        codec = _parse_codec(d.pop("codec", UNSET))

        def _parse_density(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        density = _parse_density(d.pop("density", UNSET))

        def _parse_fallback(data: object) -> None | StreamBeaconFallbackType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                fallback_type_0 = StreamBeaconFallbackType0(data)

                return fallback_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | StreamBeaconFallbackType0 | Unset, data)

        fallback = _parse_fallback(d.pop("fallback", UNSET))

        def _parse_first_frame_ms(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        first_frame_ms = _parse_first_frame_ms(d.pop("first_frame_ms", UNSET))

        def _parse_fps(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        fps = _parse_fps(d.pop("fps", UNSET))

        def _parse_kbps(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        kbps = _parse_kbps(d.pop("kbps", UNSET))

        def _parse_latency_ms(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        latency_ms = _parse_latency_ms(d.pop("latency_ms", UNSET))

        def _parse_relay(data: object) -> None | StreamBeaconRelayType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                relay_type_0 = StreamBeaconRelayType0(data)

                return relay_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | StreamBeaconRelayType0 | Unset, data)

        relay = _parse_relay(d.pop("relay", UNSET))

        stream_beacon = cls(
            seconds=seconds,
            transport=transport,
            codec=codec,
            density=density,
            fallback=fallback,
            first_frame_ms=first_frame_ms,
            fps=fps,
            kbps=kbps,
            latency_ms=latency_ms,
            relay=relay,
        )

        return stream_beacon
