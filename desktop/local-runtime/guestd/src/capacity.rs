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
/// checkpoint that has to write out a full container's worth of buffers.
/// Redis's `SIGTERM` is a `SHUTDOWN` that fsyncs the append-only file and
/// writes the snapshot its save points ask for, and is as quick. The number is
/// Postgres's.
pub(crate) const CORE_STOP_GRACE_SECONDS: u32 = 15;

/// The core containers that keep nothing of their own, by name.
///
/// SuperTokens stores every session and user in Postgres. And it never answers
/// `SIGTERM`: PID 1 in its image is the `supertokens` CLI, which starts the
/// real server as a second JVM and waits on it, and its shutdown hook only
/// joins that wait -- the signal never reaches the server. Every stop spent the
/// whole data-service grace on it, fifteen seconds of every quit, and ended in
/// the SIGKILL it could have had at once.
// Not named `..._CORE_CONTAINERS`: the runtime manager's budget test finds
// the core list's declaration in this file by its text.
pub(crate) const STATELESS_SERVICES: [&str; 1] = ["lemma-core-supertokens"];

/// How long a stateless core container is given: it has nothing to flush.
pub(crate) const STATELESS_STOP_GRACE_SECONDS: u32 = 1;

/// The core containers' OOM preference: far below any sandbox's.
pub(crate) const CORE_OOM_SCORE_ADJ: i32 = -900;

/// Free space on the data disk below which no sandbox is started.
///
/// Everything in the guest shares that disk, and it is a fixed size; the
/// first thing to notice it filling was PostgreSQL refusing to write. A new
/// sandbox is where growth starts -- an image unpacked, a workspace written --
/// so it is refused while there is still room for the database to keep
/// working, and the person is told what to free.
pub(crate) const SANDBOX_DISK_FLOOR_BYTES: u64 = 2 * 1024 * 1024 * 1024;

/// Refuse a new sandbox when the data disk is below the floor. A disk that
/// cannot be measured is not a reason to refuse.
pub(crate) fn admit_disk(free_bytes: Option<u64>) -> Result<(), GuestError> {
    match free_bytes {
        Some(free) if free < SANDBOX_DISK_FLOOR_BYTES => Err(GuestError {
            code: "resource_capacity".into(),
            message: format!(
                "The private runtime's disk is nearly full ({} MiB free), so no new \
                 sandbox was started. Delete workspaces or files you no longer need.",
                free / (1024 * 1024),
            ),
            retryable: true,
            status_code: 429,
        }),
        _ => Ok(()),
    }
}

/// The data services, in no particular order -- each is stopped on its own.
pub(crate) const CORE_CONTAINERS: [&str; 3] = ["supertokens", "redis", "postgres"];

/// The longest a guest stop can take before the engine has killed everything.
///
/// A sum, and deliberately a bound rather than a forecast. The stops now run
/// side by side -- one `nerdctl stop` per container, so the core costs its
/// slowest member rather than all three -- but an engine is free to serialise
/// them underneath, and a budget that assumed it would not is the kind that
/// cuts a database off. The host's `system.shutdown` budget must exceed it:
/// a budget below this terminates the guest while a database is still
/// checkpointing, which is the failure this arithmetic exists to prevent.
/// Computed from `MAX_SANDBOX_CEILING`, not from the default. An installation
/// that raised `LEMMA_GUEST_MAX_SANDBOXES` still has to fit, and the ceiling is
/// what bounds how far it can raise it -- computed from the default, an
/// override of 32 needed seventeen seconds more than the host was willing to
/// wait, and the guest would have been terminated mid-shutdown by the very
/// arithmetic meant to prevent that.
///
/// It covers the containers Lemma creates. One nobody here started, running in
/// this guest without the sandbox label, is given the longer grace -- more time
/// rather than less, which is the safe direction -- and extends a stop past
/// this number.
pub(crate) const GUEST_STOP_WORST_CASE_SECONDS: u32 = SANDBOX_STOP_GRACE_SECONDS
    * MAX_SANDBOX_CEILING as u32
    + CORE_STOP_GRACE_SECONDS * CORE_CONTAINERS.len() as u32;

// Checked when this file compiles, because they are statements about the
// constants above rather than about any run.
const _: () = assert!(
    CORE_STOP_GRACE_SECONDS > SANDBOX_STOP_GRACE_SECONDS,
    "a data service must not be given less grace than a scratch workload",
);
const _: () = assert!(
    STATELESS_STOP_GRACE_SECONDS <= CORE_STOP_GRACE_SECONDS,
    "a service with nothing to flush must not hold a stop longer than a database",
);
const _: () = assert!(
    MAX_SANDBOX_CEILING >= DEFAULT_MAX_SANDBOXES,
    "the ceiling cannot sit below the default, or the default is unreachable",
);
// `lemma-runtime-manager` needs this number and cannot link this crate, so its
// test reads these declarations out of this file. Changing any of them changes
// the host's shutdown budget with it.
const _: () = assert!(
    GUEST_STOP_WORST_CASE_SECONDS == 75,
    "the host's shutdown budget is derived from this; both move together",
);

/// What a stop actually stopped, split by what each class stood to lose.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct StoppedContainers {
    pub(crate) sandboxes: usize,
    pub(crate) core: usize,
    /// How long each phase took, so a slow stop names what it waited on.
    pub(crate) sandboxes_ms: u64,
    pub(crate) core_ms: u64,
}

impl StoppedContainers {
    pub(crate) fn total(self) -> usize {
        self.sandboxes + self.core
    }
}

/// Each running core container with the grace it gets.
///
/// Stateless only when the engine named it so; everything else -- the
/// databases, and anything unrecognised -- keeps the data-service grace.
pub(crate) fn core_stop_plan(core: &[String], stateless: &[String]) -> Vec<(String, u32)> {
    core.iter()
        .map(|id| {
            let grace = if stateless.contains(id) {
                STATELESS_STOP_GRACE_SECONDS
            } else {
                CORE_STOP_GRACE_SECONDS
            };
            (id.clone(), grace)
        })
        .collect()
}

pub(crate) fn elapsed_ms(started: Instant) -> u64 {
    u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX)
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

/// The most sandboxes any override may ask for.
///
/// The override raises the ceiling for a larger guest; it does not get to move
/// a number on the other side of a socket. `max_sandboxes()` clamps to this so
/// the stop budget below covers whatever an installation actually runs.
pub(crate) const MAX_SANDBOX_CEILING: usize = 30;

/// The concurrent-sandbox ceiling, overridable for a larger guest.
pub(crate) fn max_sandboxes() -> usize {
    std::env::var("LEMMA_GUEST_MAX_SANDBOXES")
        .ok()
        .and_then(|value| value.trim().parse::<usize>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_MAX_SANDBOXES)
        .min(MAX_SANDBOX_CEILING)
}

/// How long a running-sandbox count is reused for the health answer.
///
/// Shorter than the host's five-second probe interval would defeat the point;
/// much longer would make `active_sandboxes` visibly stale in a status pane.
const SANDBOX_COUNT_TTL: std::time::Duration = std::time::Duration::from_secs(15);

impl<E: Engine + 'static> GuestService<E> {
    /// The count, reused if it was taken recently.
    ///
    /// For the health answer only. Counting forks `nerdctl ps`, and the host
    /// asks every five seconds for as long as the app is open, so an idle
    /// machine spent a containerd CLI process 17,280 times a day being told
    /// the same number. Admission calls the uncached version deliberately: a
    /// stale count there would let a sandbox start that should not.
    pub(crate) fn cached_running_sandbox_count(&self) -> Result<usize, GuestError> {
        if let Ok(cache) = self.sandbox_count_cache.lock() {
            if let Some((taken_at, count)) = *cache {
                if taken_at.elapsed() < SANDBOX_COUNT_TTL {
                    return Ok(count);
                }
            }
        }
        let count = self.running_sandbox_count()?;
        if let Ok(mut cache) = self.sandbox_count_cache.lock() {
            *cache = Some((Instant::now(), count));
        }
        Ok(count)
    }

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

        admit_disk(data_disk_space(&self.state_root).map(|(free, _)| free))?;

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
        // Classified by the label the sandboxes carry, asked of the engine
        // twice, rather than by inspecting each core container by name.
        //
        // `inspect_raw` turns any non-zero exit into "no such container", so an
        // inspect that failed for any other reason -- a busy engine, a
        // truncated response -- would have taken the database out of the core
        // set and stopped it with the one-second sandbox grace. That is exactly
        // the defect this function exists to fix, reachable through a transient
        // failure, and silent when it happened.
        //
        // `sandbox_run` is the only thing that applies MANAGED_LABEL, so
        // everything else running here is core by definition. Anything
        // unexpected therefore lands in the group that waits longer, which is
        // the safe direction to be wrong in.
        let running = self.running_container_ids()?;
        let sandboxes = self.running_sandbox_ids()?;
        let core: Vec<String> = running
            .iter()
            .filter(|id| !sandboxes.contains(*id))
            .cloned()
            .collect();

        let stateless = if core.is_empty() {
            Vec::new()
        } else {
            self.running_stateless_core_ids()
        };

        // Sandboxes before the data services, so nothing is still working
        // while the database it might be working through is going away. Within
        // each phase every container is stopped at once: one `nerdctl stop`
        // over several ids works through them in turn, so the phase cost the
        // sum of its members' graces instead of the slowest one's.
        let started = Instant::now();
        let sandbox_stops: Vec<(String, u32)> = sandboxes
            .iter()
            .map(|id| (id.clone(), SANDBOX_STOP_GRACE_SECONDS))
            .collect();
        self.stop_concurrently(&sandbox_stops)?;
        let sandboxes_ms = elapsed_ms(started);

        let started = Instant::now();
        self.stop_concurrently(&core_stop_plan(&core, &stateless))?;
        Ok(StoppedContainers {
            sandboxes: sandboxes.len(),
            core: core.len(),
            sandboxes_ms,
            core_ms: elapsed_ms(started),
        })
    }

    /// Stop each container with its own grace, all at once, and wait for every one.
    ///
    /// The first failure is reported, but only after the rest have been asked:
    /// giving up part-way would leave a database running under a guest that is
    /// about to power off.
    fn stop_concurrently(&self, stops: &[(String, u32)]) -> Result<(), GuestError> {
        let results: Vec<Result<String, GuestError>> = thread::scope(|scope| {
            let workers: Vec<_> = stops
                .iter()
                .map(|(id, grace)| {
                    scope.spawn(move || {
                        self.run_checked(&[
                            "stop".into(),
                            "--time".into(),
                            grace.to_string(),
                            id.clone(),
                        ])
                    })
                })
                .collect();
            workers
                .into_iter()
                .map(|worker| {
                    worker
                        .join()
                        .unwrap_or_else(|_| Err(GuestError::engine("a container stop panicked")))
                })
                .collect()
        });
        results
            .into_iter()
            .find(Result::is_err)
            .unwrap_or(Ok(String::new()))?;
        Ok(())
    }

    /// The running core containers that keep nothing of their own.
    ///
    /// Asked by name, and allowed to fail: an engine that cannot answer leaves
    /// every core container on the longer grace, which is slower and safe.
    fn running_stateless_core_ids(&self) -> Vec<String> {
        let mut ids = Vec::new();
        for name in STATELESS_SERVICES {
            let answer = self
                .run_checked(&[
                    "ps".into(),
                    "--quiet".into(),
                    "--filter".into(),
                    format!("name=^{name}$"),
                ])
                .and_then(|output| parse_container_ids(&output));
            match answer {
                Ok(found) => ids.extend(found),
                Err(error) => eprintln!(
                    "lemma-guestd: could not find {name} to stop it briefly; \
                     it gets the data-service grace instead: {}",
                    error.message
                ),
            }
        }
        ids
    }

    fn running_container_ids(&self) -> Result<Vec<String>, GuestError> {
        let output = self.run_checked(&["ps".into(), "--quiet".into()])?;
        parse_container_ids(&output)
    }

    /// The sandboxes, by the label only a sandbox carries.
    ///
    /// The same question `running_sandbox_count` asks, for the same reason: it
    /// is the engine's own answer, and it cannot mistake a data service for a
    /// workspace the way a failed inspection could.
    fn running_sandbox_ids(&self) -> Result<Vec<String>, GuestError> {
        let output = self.run_checked(&[
            "ps".into(),
            "--quiet".into(),
            "--filter".into(),
            format!("label={MANAGED_LABEL}"),
        ])?;
        parse_container_ids(&output)
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
