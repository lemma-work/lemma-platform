"""How much memory this sandbox has left, as the kernel accounts for *it*.

`/proc/meminfo` is not namespaced. Measured inside a container limited to
2 GiB:

    MemTotal:  9214732 kB   (8.8 GiB -- the host's)
    nproc:     8            (the host's)
    memory.max: 2147483648  (2 GiB -- the sandbox's)

So a guard reading `MemAvailable` is watching the machine and saying nothing
about the box. On a roomy host it can never fire; on a busy one it fires for
reasons that have nothing to do with this sandbox. Chrome has the same blind
spot and it is worse there -- it sizes its renderer limit and its V8 heaps
off that 8.8 GiB, which is why a 2 GB sandbox was measured running 34
renderers.

Top-level and standard-library-only on purpose: this has to ship into the
E2B template, where `sandbox_runtime.workspace` deliberately does not, and
`test_e2b_templates_ship_their_imports` enforces that whatever a shipped
module imports ships too.

**Reading it correctly matters more than reading it.** Two traps, both
measured:

- `memory.current` and `memory.peak` include reclaimable page cache. Reading
  3 GB of file data through the cgroup drove `peak` to exactly `memory.max`
  with `oom 0`, `oom_kill 0`, 614 successful reclaims and `anon` going
  *down*. Neither is ever a reason to act.
- In cgroup v2 `shmem` is a subset of `file`, so `anon + file` double-counts
  tmpfs. `anon + shmem` does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: Where the kernel puts a cgroup-v2 controller when the container has its
#: own namespace, which is Docker's default.
_CGROUP_ROOT = Path("/sys/fs/cgroup")
_MEMINFO = Path("/proc/meminfo")


@dataclass(frozen=True, slots=True)
class SandboxMemory:
    """One reading. Every field independently unknown.

    Nothing here is a single number the caller should threshold blindly, and
    each file is read on its own so that one unreadable path does not lose
    the others.
    """

    limit_mb: int | None = None
    current_mb: int | None = None
    anon_mb: int | None = None
    shmem_mb: int | None = None
    unevictable_mb: int | None = None
    slab_unreclaimable_mb: int | None = None
    swap_current_mb: int | None = None
    swap_max_mb: int | None = None
    oom_kill: int | None = None
    #: `memory.pressure`, the `full` line's `avg10`. Absent on kernels built
    #: without PSI -- measured absent on Docker Desktop's, so nothing may
    #: depend on it.
    pressure_full_avg10: float | None = None
    #: `/proc/meminfo`'s `MemAvailable`, in MB. The host's number, kept as
    #: the fallback for a fabric with no cgroup to read.
    available_mb: int | None = None

    @property
    def unreclaimable_mb(self) -> int | None:
        """What reclaim cannot take back.

        `shmem` is counted once and deliberately: it is the part of `file`
        that is not reclaimable, and adding `file` instead would count it
        twice. Small in this image -- the workspace gets no tmpfs and Chrome
        is steered off `/dev/shm` -- and included anyway, because it is free
        and it is the term that goes wrong first if anyone adds one.
        """
        parts = [
            self.anon_mb,
            self.shmem_mb,
            self.unevictable_mb,
            self.slab_unreclaimable_mb,
        ]
        if all(part is None for part in parts):
            return None
        return sum(part or 0 for part in parts)

    @property
    def headroom_mb(self) -> int | None:
        """Room between what cannot be reclaimed and the hard limit."""
        unreclaimable = self.unreclaimable_mb
        if self.limit_mb is None or unreclaimable is None:
            return None
        return self.limit_mb - unreclaimable


def _read(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def _bytes_to_mb(raw: str | None) -> int | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text or text == "max":
        return None
    try:
        return int(text) // (1024 * 1024)
    except ValueError:
        return None


def _keyed(raw: str | None) -> dict[str, int]:
    """`memory.stat`/`memory.events`: `name value` per line, order-free.

    Unknown keys are kept rather than rejected; these files grow between
    kernel versions and a parser that insisted on a shape would break on an
    upgrade rather than on a mistake.
    """
    found: dict[str, int] = {}
    for line in (raw or "").splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            found[parts[0]] = int(parts[1])
        except ValueError:
            continue
    return found


def _avg10(raw: str | None) -> float | None:
    for line in (raw or "").splitlines():
        if not line.startswith("full "):
            continue
        for field in line.split():
            name, _, value = field.partition("=")
            if name == "avg10":
                try:
                    return float(value)
                except ValueError:
                    return None
    return None


def available_memory_mb(meminfo: Path = _MEMINFO) -> int | None:
    """`MemAvailable`, in MB. The host's answer, not the sandbox's.

    Kept because it is the only answer on a fabric with no cgroup file to
    read, and because on a Firecracker guest -- which is what E2B runs -- the
    guest kernel reports the VM's own memory, so there it is honest.
    """
    for line in (_read(meminfo) or "").splitlines():
        if line.startswith("MemAvailable:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(parts[1]) // 1024
                except ValueError:
                    return None
    return None


def read_memory(root: Path = _CGROUP_ROOT, meminfo: Path = _MEMINFO) -> SandboxMemory:
    """One reading of everything worth knowing, each file independently."""
    stat = _keyed(_read(root / "memory.stat"))
    events = _keyed(_read(root / "memory.events"))

    def megabytes(key: str) -> int | None:
        raw = stat.get(key)
        return None if raw is None else raw // (1024 * 1024)

    return SandboxMemory(
        limit_mb=_bytes_to_mb(_read(root / "memory.max")),
        current_mb=_bytes_to_mb(_read(root / "memory.current")),
        anon_mb=megabytes("anon"),
        shmem_mb=megabytes("shmem"),
        unevictable_mb=megabytes("unevictable"),
        slab_unreclaimable_mb=megabytes("slab_unreclaimable"),
        swap_current_mb=_bytes_to_mb(_read(root / "memory.swap.current")),
        swap_max_mb=_bytes_to_mb(_read(root / "memory.swap.max")),
        oom_kill=events.get("oom_kill"),
        pressure_full_avg10=_avg10(_read(root / "memory.pressure")),
        available_mb=available_memory_mb(meminfo),
    )


__all__ = ["SandboxMemory", "available_memory_mb", "read_memory"]
