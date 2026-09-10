//! What this guest can still fit, and reclaiming what it cannot.

use super::*;

pub(crate) fn guest_available_memory_bytes() -> Result<u64, GuestError> {
    let meminfo = fs::read_to_string("/proc/meminfo")
        .map_err(|error| GuestError::engine(format!("could not read guest memory: {error}")))?;
    parse_mem_available(&meminfo)
        .ok_or_else(|| GuestError::engine("guest available memory was unavailable"))
}

/// Split out so it can be tested without a `/proc`.
pub(crate) fn parse_mem_available(meminfo: &str) -> Option<u64> {
    meminfo
        .lines()
        .find_map(|line| line.strip_prefix("MemAvailable:"))
        .and_then(|value| value.split_whitespace().next())
        .and_then(|value| value.parse::<u64>().ok())
        .and_then(|kib| kib.checked_mul(1024))
}

/// How long a sandbox is given to stop.
///
/// What a sandbox can lose is one in-flight write into its own workspace. A
/// second is enough for a signal handler to finish that write, and short
/// enough that `max_sandboxes()` of them stay inside the host's budget.
pub(crate) const SANDBOX_STOP_GRACE_SECONDS: u32 = 1;

/// How long the data services are given to stop.
///
/// Postgres's `SIGINT` is a fast shutdown: roll back what is open, checkpoint,
/// exit. Ordinarily well under a second here; the case this covers is a
/// checkpoint that has to write out a full container's worth of buffers. Redis
/// is quick and SuperTokens keeps nothing of its own, so the number is
/// Postgres's.
pub(crate) const CORE_STOP_GRACE_SECONDS: u32 = 15;

/// The data services, in no particular order -- the stop is one engine call.
pub(crate) const CORE_CONTAINERS: [&str; 3] = ["supertokens", "redis", "postgres"];

/// The longest a guest stop can take before the engine has killed everything.
///
/// `nerdctl stop` works through its arguments one at a time, so this is the sum
/// rather than the maximum. The host's `system.shutdown` budget must exceed it:
/// a budget below this terminates the guest while a database is still
/// checkpointing, which is the failure this arithmetic exists to prevent.
pub(crate) const GUEST_STOP_WORST_CASE_SECONDS: u32 = SANDBOX_STOP_GRACE_SECONDS
    * DEFAULT_MAX_SANDBOXES as u32
    + CORE_STOP_GRACE_SECONDS * CORE_CONTAINERS.len() as u32;

/// What a stop actually stopped, split by what each class stood to lose.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct StoppedContainers {
    pub(crate) sandboxes: usize,
    pub(crate) core: usize,
}

impl StoppedContainers {
    pub(crate) fn total(self) -> usize {
        self.sandboxes + self.core
    }
}

/// The ids `ps --quiet` printed, refusing anything that is not one.
pub(crate) fn parse_container_ids(output: &str) -> Result<Vec<String>, GuestError> {
    output
        .lines()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(|value| {
            if value.len() > 128 || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
                return Err(GuestError::engine(
                    "container engine returned an invalid container identifier",
                ));
            }
            Ok(value.to_owned())
        })
        .collect()
}

/// The concurrent-sandbox ceiling, overridable for a larger guest.
pub(crate) fn max_sandboxes() -> usize {
    std::env::var("LEMMA_GUEST_MAX_SANDBOXES")
        .ok()
        .and_then(|value| value.trim().parse::<usize>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_MAX_SANDBOXES)
}

impl<E: Engine + 'static> GuestService<E> {
    pub(crate) fn running_sandbox_count(&self) -> Result<usize, GuestError> {
        let output = self.run_checked(&[
            "ps".into(),
            "--quiet".into(),
            "--filter".into(),
            format!("label={MANAGED_LABEL}"),
        ])?;
        Ok(output
            .lines()
            .filter(|line| !line.trim().is_empty())
            .count())
    }

    /// Decide whether another sandbox can start, from what the guest is
    /// actually using rather than from what its containers are allowed to use.
    ///
    /// The previous version summed every running sandbox's `--memory` ceiling
    /// and treated the total as spoken for. Ceilings are not reservations:
    /// containerd sets `memory.max` and the container occupies only the pages
    /// it touches. With a 1536 MiB core reservation and a 2048 MiB default
    /// ceiling, a 6 GiB guest therefore admitted exactly two sandboxes and
    /// refused the third -- so opening a workspace or two made every function
    /// fail, and the refusal reached the user as a 120-second deadline with no
    /// mention of memory.
    ///
    /// `requested` is still validated by `validate_resources`; it is the
    /// ceiling for this sandbox, not a claim on the guest.
    pub(crate) fn admit_sandbox_memory(&self, requested: u64) -> Result<(), GuestError> {
        let running = self.running_sandbox_count()?;
        let ceiling = max_sandboxes();
        if running >= ceiling {
            return Err(GuestError {
                code: "resource_capacity".into(),
                message: format!(
                    "This computer is already running {running} sandboxes, which is the \
                     configured maximum. Close a workspace, or raise \
                     LEMMA_GUEST_MAX_SANDBOXES."
                ),
                retryable: true,
                status_code: 429,
            });
        }

        let available = guest_available_memory_bytes()?;
        let needed = SANDBOX_MEMORY_REQUEST_BYTES.saturating_add(GUEST_MEMORY_HEADROOM_BYTES);
        if available < needed {
            return Err(GuestError {
                code: "resource_capacity".into(),
                message: format!(
                    "Not enough memory left in the private runtime to start another \
                     sandbox: {} MiB free, {} MiB needed. {running} sandboxes are \
                     running; closing one frees memory immediately.",
                    available / (1024 * 1024),
                    needed / (1024 * 1024),
                ),
                retryable: true,
                status_code: 429,
            });
        }
        let _ = requested;
        Ok(())
    }

    /// Every sandbox container this guest owns, by label rather than by name.
    pub(crate) fn remove_managed_sandbox_containers(&self) -> Result<usize, GuestError> {
        let output = self.run_checked(&[
            "ps".into(),
            "--all".into(),
            "--quiet".into(),
            "--filter".into(),
            format!("label={MANAGED_LABEL}"),
        ])?;
        let ids: Vec<String> = output
            .lines()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(|value| {
                if value.len() > 128 || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
                    return Err(GuestError::engine(
                        "container engine returned an invalid container identifier",
                    ));
                }
                Ok(value.to_owned())
            })
            .collect::<Result<_, _>>()?;
        if ids.is_empty() {
            return Ok(0);
        }
        let mut arguments = vec!["rm".into(), "--force".into()];
        arguments.extend(ids.iter().cloned());
        self.run_checked(&arguments)?;
        Ok(ids.len())
    }

    /// Remove every workspace directory, with `purge_workspace`'s discipline.
    ///
    /// What actually stops this clearing the guest is the `is_dir()` below,
    /// which is `entry.file_type()` and so does *not* follow a symlink: a
    /// symlinked entry is skipped rather than followed into.
    ///
    /// The parent re-check is belt to that braces and cannot fire on its own --
    /// `read_dir` yields `root.join(name)`, so the parent is `root` by
    /// construction, `..` included. Kept because it costs nothing and because
    /// the day someone changes how these paths are built is the day it starts
    /// mattering. The comment used to credit it with the symlink defence, which
    /// is the wrong line to trust.
    pub(crate) fn remove_all_workspaces(&self) -> Result<usize, GuestError> {
        let root = self.state_root.join("workspaces");
        let Ok(entries) = fs::read_dir(&root) else {
            return Ok(0);
        };
        let mut removed = 0;
        for entry in entries.flatten() {
            let path = entry.path();
            if path.parent() != Some(root.as_path()) {
                return Err(GuestError::invalid("workspace escaped managed root"));
            }
            if !entry.file_type().is_ok_and(|kind| kind.is_dir()) {
                continue;
            }
            fs::remove_dir_all(&path).map_err(|error| GuestError::engine(error.to_string()))?;
            removed += 1;
        }
        Ok(removed)
    }

    /// Stop everything, giving each container a grace period that matches what
    /// it stands to lose.
    ///
    /// This used to be one `stop --time 5` over every running container. Two
    /// things were wrong with that, and the second is why Postgres was killed.
    ///
    /// Five seconds is the wrong number for a database. The official image's
    /// `STOPSIGNAL` is `SIGINT`, which is Postgres's *fast* shutdown: roll back
    /// what is open, checkpoint, exit. Usually under a second for an
    /// installation this size, and a checkpoint that has to write out a full
    /// 512 MiB container's buffers is not. Past the grace the engine sends
    /// SIGKILL, and the next start replays the WAL instead of opening.
    ///
    /// And the total never fitted. `nerdctl stop` works through its arguments
    /// one at a time, so the worst case was five seconds times every running
    /// container -- up to `max_sandboxes()` of them -- inside a host request
    /// budget of eight seconds. Whichever container was still stopping when
    /// that expired had the whole guest terminated underneath it.
    ///
    /// So: sandboxes first and briefly, because what a sandbox can lose is one
    /// in-flight write into its own workspace; then the core, with a grace a
    /// database can actually use. The numbers are bounds chosen from what each
    /// container holds rather than measurements -- `GUEST_STOP_WORST_CASE` is
    /// the arithmetic they add up to, and the host's budget has to exceed it.
    pub(crate) fn stop_all_containers(&self) -> Result<StoppedContainers, GuestError> {
        let running = self.running_container_ids()?;
        let core = self.core_container_ids()?;
        let sandboxes: Vec<String> = running
            .iter()
            .filter(|id| !core.contains(*id))
            .cloned()
            .collect();

        // Sandboxes before the data services, so nothing is still working
        // while the database it might be working through is going away.
        self.stop_containers(&sandboxes, SANDBOX_STOP_GRACE_SECONDS)?;
        self.stop_containers(&core, CORE_STOP_GRACE_SECONDS)?;
        Ok(StoppedContainers {
            sandboxes: sandboxes.len(),
            core: core.len(),
        })
    }

    fn stop_containers(&self, ids: &[String], grace_seconds: u32) -> Result<(), GuestError> {
        if ids.is_empty() {
            return Ok(());
        }
        let mut arguments = vec!["stop".into(), "--time".into(), grace_seconds.to_string()];
        arguments.extend(ids.iter().cloned());
        self.run_checked(&arguments)?;
        Ok(())
    }

    fn running_container_ids(&self) -> Result<Vec<String>, GuestError> {
        let output = self.run_checked(&["ps".into(), "--quiet".into()])?;
        parse_container_ids(&output)
    }

    /// The data services, by the names `ensure_*` gives them.
    ///
    /// Asked for by name rather than filtered out of the full list, so a
    /// container the engine reports in an unexpected shape is treated as a
    /// sandbox -- stopped briefly -- rather than silently given the database's
    /// grace, or missed.
    fn core_container_ids(&self) -> Result<Vec<String>, GuestError> {
        let mut ids = Vec::new();
        for name in CORE_CONTAINERS {
            let container = format!("lemma-core-{name}");
            if let Some(id) = self.container_id(&container)? {
                ids.push(id);
            }
        }
        Ok(ids)
    }
}

/// How much room is left where the guest keeps its data.
///
/// Reported on every health call, because nothing reported it at all and the
/// disk it describes is a fixed size. Everything the guest holds shares it --
/// container images, the unpacked snapshots, every workspace, the database --
/// and the first sign that it had run out was whatever broke first, which is
/// usually Postgres refusing to write.
///
/// `None` rather than a guess when the filesystem cannot be measured. A
/// fabricated zero reads as "full" and a fabricated large number reads as
/// "fine"; both are worse than saying nothing, and the host already treats an
/// absent field as an older guest.
pub(crate) fn data_disk_space(root: &Path) -> Option<(u64, u64)> {
    use std::os::unix::ffi::OsStrExt;
    let path = std::ffi::CString::new(root.as_os_str().as_bytes()).ok()?;
    // SAFETY: `statvfs` only writes the struct it is handed, and the path is a
    // NUL-terminated string that outlives the call. The return value is checked.
    let mut measured: libc::statvfs = unsafe { std::mem::zeroed() };
    if unsafe { libc::statvfs(path.as_ptr(), &mut measured) } != 0 {
        return None;
    }
    // `f_bavail`, not `f_bfree`: the blocks an unprivileged process may
    // actually use. The containers writing here are not root on the host
    // filesystem, and reserved blocks are not space they can have.
    let block = u64::from(measured.f_frsize as u32);
    Some((
        u64::from(measured.f_bavail as u32).saturating_mul(block),
        u64::from(measured.f_blocks as u32).saturating_mul(block),
    ))
}
