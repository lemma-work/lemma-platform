"""The link's ``compute`` role: a connected machine, not a coding-agent host.

See docs/architecture/connected-machines.md. A person joins their own laptop or
VM to Lemma with ``lemma machine connect``; the agent that command starts opens
the same ``/agent-host/link`` socket Desktop's Agent Host does, and says
``role: "compute"`` on its first frame. Everything about the socket is shared --
the envelope, ``error``, ``reconnect``, the heartbeat, the close codes and what
the machine does with each. What differs is what travels on it:

* ``pair`` waits for a person to approve the code in the browser, and
  ``paired`` hands back a per-machine secret rather than a host secret;
* ``hello`` and ``control`` report the machine (its mode, home, capabilities
  and the runtime it is serving) instead of run capacity;
* every connection Lemma makes into the machine -- the workspace runtime, the
  browser relay, a preview -- is a stream in a binary mux frame
  (``sandbox_runtime.machine_link.mux``). There are no ``op``, ``events`` or
  ``mcp`` frames on a compute link.

Kept beside ``agent_host_link`` rather than in it so that file stays the Agent
Host's vocabulary; both are held to the same wire contract fixture.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.agent.domain.agent_host import (
    AGENT_HOST_PROTOCOL_VERSION,
    HostHello,
)


#: A compute link needs protocol 4, which is where the role was added.
COMPUTE_MIN_PROTOCOL_VERSION = 4

#: The longest a ``pair`` waits for the person to approve the code. The code
#: itself expires at the same moment, so a machine left waiting is told why.
COMPUTE_PAIRING_TTL_SECONDS = 10 * 60


class LinkRole(str, Enum):
    """What the machine at the other end of a link is for.

    Absent on the first frame means ``agent_host``: every Desktop released
    before the role existed is one.
    """

    AGENT_HOST = "agent_host"
    COMPUTE = "compute"


class MachineMode(str, Enum):
    """How the machine serves the workspace runtime.

    ``host`` runs it as the person's own OS user, in a uv-managed environment
    under ``--root``; ``image`` runs the ``lemma-workspace`` image with Docker,
    which is the same machine an E2B or Docker sandbox is, browser included.
    """

    HOST = "host"
    IMAGE = "image"


class StreamTargetKind(str, Enum):
    """What a mux ``OPEN`` may ask the machine for.

    ``port`` is a TCP port on the machine's loopback (the runtime, the browser
    relay, a dev server); ``runtime_bundle`` is the runtime overlay, pushed by
    Lemma, which the machine installs and restarts its runtime on.
    """

    PORT = "port"
    RUNTIME_BUNDLE = "runtime_bundle"


class MachineCapabilities(BaseModel):
    """What the machine says it can do. Reported, never assumed.

    ``browser`` needs a display and Chrome on a host-mode machine and is always
    true in image mode; ``docker`` is a reachable Docker daemon (``--docker``
    mounts the host's socket into image mode). Anything else a newer machine
    reports is kept, not refused.
    """

    model_config = ConfigDict(extra="allow")

    processes: bool = True
    files: bool = True
    python: bool = True
    browser: bool = False
    docker: bool = False
    previews: bool = True


class MachineReport(BaseModel):
    """The machine's own account of itself, on ``hello`` and every ``control``."""

    mode: MachineMode
    #: ``macos`` / ``linux`` / ``windows``.
    platform: str = Field(min_length=1, max_length=32)
    #: Where the runtime's home is on this machine: ``--root`` in host mode,
    #: ``/home/user`` in image mode. Paths Lemma sends are under it.
    home: str = Field(min_length=1, max_length=4096, pattern=r"^/")
    #: The runtime overlay the machine is serving; None until it has one.
    runtime_version: str | None = Field(default=None, max_length=128)
    #: The runtime answered its own health check on the machine.
    runtime_ready: bool = False
    #: The loopback port the runtime listens on.
    runtime_port: int = Field(ge=1, le=65535)
    #: The browser relay's port, when the machine has a browser.
    relay_port: int | None = Field(default=None, ge=1, le=65535)
    capabilities: MachineCapabilities = Field(default_factory=MachineCapabilities)


# ---------------------------------------------------------------- machine frames


class ComputePairBody(BaseModel):
    """``pair`` on a compute link: claim the connection a person approved.

    ``claim_key`` is the secret half of the pairing. The code is shown to the
    person and typed or clicked in the browser, so it is not secret; the claim
    key never leaves the machine except here, and only the machine that asked
    for the code holds it. Without it, anyone who saw the code could take the
    machine's secret the moment the person approved.
    """

    role: Literal[LinkRole.COMPUTE]
    pairing_code: str = Field(min_length=6, max_length=32)
    claim_key: str = Field(min_length=32, max_length=128)
    hello: HostHello


class ComputeHelloBody(BaseModel):
    role: Literal[LinkRole.COMPUTE]
    hello: HostHello
    machine: MachineReport


class ComputeControlBody(BaseModel):
    """The heartbeat. Carries the report so a runtime restart or a capability
    change reaches Lemma within one beat."""

    machine: MachineReport


# ---------------------------------------------------------------- server frames


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ComputePairedBody(BaseModel):
    """The machine's credential, shown exactly once."""

    connection_id: UUID
    user_id: UUID
    name: str
    machine_secret: str


class RuntimeGrant(BaseModel):
    """What the machine's runtime is started with.

    ``token`` is the credential Lemma presents to the runtime
    (``X-Lemma-Runtime-Token``), derived per connection on the server and sent
    only on an authenticated link. ``version`` is the overlay Lemma will push;
    None when this server has no bundle to push.
    """

    token: str = Field(min_length=16)
    version: str | None = None


class ComputeWelcomeBody(BaseModel):
    connection_id: UUID
    user_id: UUID
    name: str
    protocol_version: int = AGENT_HOST_PROTOCOL_VERSION
    heartbeat_ms: int
    server_time: datetime = Field(default_factory=_utc_now)
    runtime: RuntimeGrant


class ComputeControlOkBody(BaseModel):
    """Nothing to say yet; an answer so the machine knows the beat landed."""


class PortTarget(BaseModel):
    kind: Literal[StreamTargetKind.PORT]
    port: int = Field(ge=1, le=65535)


class RuntimeBundleTarget(BaseModel):
    kind: Literal[StreamTargetKind.RUNTIME_BUNDLE]
    version: str = Field(min_length=1, max_length=128)
    #: The archive file's digest, which the machine checks before installing;
    #: ``version`` digests the unpacked contents and is a different number.
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    size: int = Field(ge=1)
    #: Modules the installed overlay must import before it is made current.
    requires: list[str] = Field(default_factory=list)
