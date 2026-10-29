"""The display's size: a few presets, and one viewer at a time who may change it.

The display used to follow the pane watching it. Every divider drag became a
RandR mode change, and a viewer's picture went away for half a second while
the server agreed the new geometry -- measured at ten remote resizes for ten
panel changes. Two panes of different shapes fought, last writer wins. Now
the pane rescales the picture locally and the display only changes when
somebody picks a preset, before connecting or from the toolbar.

**Presets, not arbitrary sizes.** A preset is a layout a site has a reason to
serve -- desktop, wide, tablet, phone -- and a fixed set is a fixed set of
framebuffers somebody else's memory pays for. A size that is not a preset is
an older frontend still asking to fit its pane; it is clamped to the resting
size, as it always was, so it keeps working while the rollout reaches it.

**The lease.** Only a viewer in control may resize, and the first one to do
so holds the size until its socket closes or it stops being in control. A
second controller (VNC is shared, so there can be two) is told who has it
instead of silently undoing their choice. The lease lives here because the
relay is per-sandbox: it is the one process that sees every viewer's socket.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import os
from pathlib import Path

from collections.abc import Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .chrome import RecordingInProgress, default_display_size, set_display_size
from .viewers import CONTROL, VIEWERS

#: The presets, by name, in CSS pixels. Changing one changes what every
#: frontend offers; `lemma-frontend/src/computer/presets.ts` lists the same.
PRESETS: dict[str, tuple[int, int]] = {
    "desktop": (1280, 800),
    "wide": (1440, 900),
    "tablet": (820, 1180),
    "phone": (390, 844),
}

_PRESET_SIZES = frozenset(PRESETS.values())

#: Who holds the size right now, by viewer name. Only meaningful while that
#: viewer is live and in control; checked on every use rather than cleared by
#: a callback, so a socket that died without a goodbye cannot keep it.
_lease: dict[str, str | None] = {"holder": None}


class DisplayResizeRequest(BaseModel):
    """The size a viewer wants the sandbox display to be.

    Bounded here rather than trusted: these numbers come from a browser
    window, and a display is a framebuffer somebody else's memory pays for.
    The script clamps again against the framebuffer Xvfb actually allocated,
    which is the limit that cannot be argued with.
    """

    width: int = Field(ge=320, le=4096)
    height: int = Field(ge=240, le=4096)
    #: The viewer asking, as named on its socket. Absent from older
    #: frontends, which never hold the lease and are clamped as before.
    viewer: str | None = Field(default=None, max_length=128)


class DisplayResizeResponse(BaseModel):
    #: What the display ended up as, which is not always what was asked for --
    #: see the clamping in `set-display-size`.
    size: str


class DisplayState(BaseModel):
    #: In device pixels; a preset is this divided by `density`.
    size: str | None
    density: int
    presets: dict[str, str]
    #: The viewer holding the size, if one is.
    lease: str | None


def lease_holder() -> str | None:
    """The live controlling viewer who holds the size, or nobody."""
    holder = _lease["holder"]
    if holder and VIEWERS.mode_of(holder) == CONTROL:
        return holder
    return None


#: What the running browser was launched at: written by `lemma-ensure-display`
#: beside its proxy stamp. A preset is in CSS pixels and the display is in
#: device pixels, so this is the factor between them.
DENSITY_STAMP = Path("/tmp/lemma-browser/density.active")


def active_density() -> int:
    """1 or 2: the device scale factor Chrome is running at right now."""
    try:
        return 2 if DENSITY_STAMP.read_text().strip() == "2" else 1
    except OSError:
        return 1


def _size_for(width: int, height: int) -> tuple[int, int]:
    """A preset as asked; anything else clamped to the resting size.

    Clamped to the resting size and not to the framebuffer ceiling: a
    maximised pane on a large monitor would otherwise leave a small sandbox
    running at 1920x1200 -- 1.67x the pixels to encode and for
    `agent-browser record` to grab, which was measured to lose a recording
    part-way through on a loaded runner.
    """
    density = active_density()
    if (width, height) in _PRESET_SIZES:
        return width * density, height * density
    cap_width, cap_height = default_display_size()
    return min(width, cap_width) * density, min(height, cap_height) * density


def _claim(viewer: str | None) -> None:
    """Take the lease for `viewer`, or say why it cannot be had.

    409 for both refusals, with a sentence the pane shows: neither is a fault,
    and both are something the person can change.
    """
    holder = lease_holder()
    if viewer is None:
        if holder is not None:
            raise HTTPException(
                status_code=409,
                detail="somebody else is controlling the screen size",
            )
        return
    if VIEWERS.mode_of(viewer) != CONTROL:
        raise HTTPException(
            status_code=409,
            detail="take control of the screen to change its size",
        )
    if holder is not None and holder != viewer:
        raise HTTPException(
            status_code=409,
            detail="somebody else is controlling the screen size",
        )
    _lease["holder"] = viewer


def register(app: FastAPI, require_token: Callable[..., Awaitable[None]]) -> None:
    """Add these routes to the relay, behind its token check."""
    guarded = [Depends(require_token)]

    @app.get("/display", response_model=DisplayState, dependencies=guarded)
    async def display_state() -> DisplayState:
        return DisplayState(
            size=await current_size(),
            density=active_density(),
            presets={name: f"{w}x{h}" for name, (w, h) in PRESETS.items()},
            lease=lease_holder(),
        )

    @app.post(
        "/display:resize", response_model=DisplayResizeResponse, dependencies=guarded
    )
    async def display_resize(request: DisplayResizeRequest) -> DisplayResizeResponse:
        """Change the display's size, for the viewer holding the lease.

        noVNC's own `resizeSession` cannot do this for us: it refuses while
        the client is view-only, and TigerVNC is started refusing it anyway,
        so that this lease is the only door.
        """
        _claim(request.viewer)
        width, height = _size_for(request.width, request.height)
        try:
            size = await set_display_size(width, height)
        except RecordingInProgress as exc:
            # 409 rather than a silent no-op, and a reason the pane can show.
            # A person changing the size while an agent is recording used to
            # move the framebuffer under the recorder; the picture is
            # letterboxed until the take ends instead, which is the
            # recoverable half of the trade.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if size is None:
            raise HTTPException(
                status_code=409, detail="the display could not be resized"
            )
        return DisplayResizeResponse(size=size)

    @app.post(
        "/display:reset", response_model=DisplayResizeResponse, dependencies=guarded
    )
    async def display_reset() -> DisplayResizeResponse:
        """Put the display back to its starting size, and free the lease.

        Called when the last person watching disconnects. Without it the
        sandbox kept whichever shape the last viewer chose for the rest of
        its life -- so an agent taking a screenshot or a recording afterwards
        inherited the dimensions of a phone it could not see and had no way
        to know about. A predictable resting size is something it can plan
        against.
        """
        _lease["holder"] = None
        density = active_density()
        width, height = (side * density for side in default_display_size())
        try:
            size = await set_display_size(width, height)
        except RecordingInProgress as exc:
            # The last viewer leaving must not resize either. This is the
            # more dangerous of the two paths, because nobody is watching
            # when it fires: the agent is alone with its recording and the
            # reset would land in the middle of it. The display keeps the
            # viewer's shape until the take ends, and the next reset -- or
            # the next viewer -- puts it back.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if size is None:
            raise HTTPException(status_code=409, detail="the display did not reset")
        return DisplayResizeResponse(size=size)


async def current_size() -> str | None:
    """What the X server says the screen is now, as `WxH`."""
    try:
        process = await asyncio.create_subprocess_exec(
            "xrandr",
            "--current",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5)
    except asyncio.TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        return None
    return parse_screen_size(stdout.decode("utf-8", "replace"))


def machine_cpus(cpu_max: Path = Path("/sys/fs/cgroup/cpu.max")) -> float | None:
    """How many CPUs this sandbox may use, which decides the stream's density.

    The cgroup's quota where there is one: in a container `os.cpu_count()`
    is the host's, and a one-core sandbox on an eight-core host would be
    streamed at a density it cannot render. A VM -- E2B, the desktop guest --
    has no quota and its own CPU count is the truth.
    """
    try:
        quota, period = cpu_max.read_text().split()[:2]
        if quota != "max":
            return round(int(quota) / int(period), 2)
    except OSError, ValueError:
        pass
    count = os.cpu_count()
    return float(count) if count else None


def parse_screen_size(xrandr: str) -> str | None:
    """`1280x800` from `Screen 0: minimum 1 x 1, current 1280 x 800, ...`."""
    for line in xrandr.splitlines():
        if not line.startswith("Screen "):
            continue
        _, _, rest = line.partition("current ")
        width, _, tail = rest.partition(" x ")
        height = tail.split(",", 1)[0].strip()
        if width.strip().isdigit() and height.isdigit():
            return f"{width.strip()}x{height}"
    return None


__all__ = [
    "PRESETS",
    "active_density",
    "DisplayResizeRequest",
    "register",
    "current_size",
    "lease_holder",
    "machine_cpus",
    "parse_screen_size",
]
