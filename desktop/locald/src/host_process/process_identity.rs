//! Proving a pid is still the process we started, before signalling it.

use super::*;

pub(crate) struct ProcessIdentity {
    pub(crate) executable: String,
    pub(crate) start_identity: String,
}

/// How long `process_identity` waits for a freshly forked process to finish
/// `exec` before giving up on naming its executable.
#[cfg(unix)]
pub(crate) const IDENTITY_SETTLE_ATTEMPTS: u32 = 20;
#[cfg(unix)]
pub(crate) const IDENTITY_SETTLE_INTERVAL: Duration = Duration::from_millis(25);
/// The backstop for `settled_process_identity`, which normally stops on one of
/// the two things it is actually watching for rather than on the clock.
#[cfg(unix)]
pub(crate) const IDENTITY_SETTLE_TIMEOUT: Duration = Duration::from_secs(5);

/// One `ps` query. `Ok(None)` means the process exists but has not yet reported
/// a usable executable name, so the caller should look again.
///
/// macOS and the BSDs only. `ps -o comm=` prints an absolute path there, which
/// is what makes this work; on Linux the same flag prints a bare command name
/// truncated to fifteen characters, so every `canonicalize` below failed and
/// every process this daemon spawned was reported as unidentifiable. Linux has
/// its own implementation, and a better one -- see the `/proc` version below.
#[cfg(all(unix, not(target_os = "linux")))]
pub(crate) fn query_process_identity(pid: &str) -> io::Result<Option<ProcessIdentity>> {
    let executable = Command::new("/bin/ps")
        .args(["-p", pid, "-o", "comm="])
        .output()?;
    let started = Command::new("/bin/ps")
        .args(["-p", pid, "-o", "lstart="])
        .output()?;
    if !executable.status.success() || !started.status.success() {
        return Err(io::Error::new(io::ErrorKind::NotFound, "process not found"));
    }
    let executable = String::from_utf8(executable.stdout)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    let executable = executable.trim();
    // `ps` brackets the name as `(sh)` while a process has forked but not yet
    // finished `exec`, and while it is exiting. That placeholder is not a path,
    // so canonicalizing it fails with ENOENT — which used to make a child that
    // was merely still starting look unidentifiable, and cost it the ownership
    // record it had just been spawned to receive.
    if executable.starts_with('(') && executable.ends_with(')') {
        return Ok(None);
    }
    // The bracket is not the only shape that transient takes. `sh -c "…"`
    // execs into the command it was given, and a name read across that
    // boundary can be unbracketed and still name nothing that resolves. That
    // is the same "not settled yet" the bracket means, so it retries too —
    // propagating ENOENT here spent none of the 500ms settle budget and
    // reported a starting child as one whose ownership could not be recorded.
    // A child that never settles still fails, with the outer error that says
    // so rather than a bare "No such file or directory".
    let Ok(executable) = Path::new(executable).canonicalize() else {
        return Ok(None);
    };
    let executable = executable.to_string_lossy().into_owned();
    let start_identity = String::from_utf8(started.stdout)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?
        .trim()
        .to_owned();
    if start_identity.is_empty() {
        return Err(io::Error::other("process start identity was empty"));
    }
    Ok(Some(ProcessIdentity {
        executable,
        start_identity,
    }))
}

/// The same question, asked of `/proc` rather than of `ps`.
///
/// Linux does not answer `ps -o comm=` with a path, so the BSD implementation
/// above could never identify anything here: `Path::new("sleep").canonicalize()`
/// fails, every sample looked like a process that had not settled, and the
/// ownership ledger -- the thing that guarantees this daemon only ever signals
/// processes it started -- recorded nothing at all.
///
/// `/proc/<pid>/exe` is a better source than `ps` in both directions. It is the
/// kernel's own answer, already absolute and already resolved through symlinks,
/// and it is readable only for a process of the same user, which is a check
/// worth having for free. `starttime` from `/proc/<pid>/stat` is likewise a
/// stronger identity than `ps lstart`, whose one-second granularity is exactly
/// the window in which a recycled PID looks like the process it replaced.
///
/// One deliberate difference from macOS. `ps` reports `(sh)` for a process that
/// has forked but not yet finished `exec`, which is the placeholder the settle
/// loop exists to wait out. `/proc/<pid>/exe` has no such state -- during that
/// window it simply names the binary the process is still running. So on Linux
/// `Ok(None)` means only "gone or not ours", and a record written mid-exec
/// names the pre-exec binary. That record then fails to match later, and a
/// ledger entry that fails to match is one this daemon declines to signal --
/// the safe direction, and the same one an unreadable link takes.
#[cfg(target_os = "linux")]
pub(crate) fn query_process_identity(pid: &str) -> io::Result<Option<ProcessIdentity>> {
    let executable = match std::fs::read_link(format!("/proc/{pid}/exe")) {
        Ok(path) => path,
        // ESRCH once the process is gone, EACCES for one we do not own, ENOENT
        // for a kernel thread. None of the three is a process this daemon may
        // claim, and none becomes one by looking again.
        Err(error) => return Err(io::Error::new(io::ErrorKind::NotFound, error)),
    };
    let stat = std::fs::read_to_string(format!("/proc/{pid}/stat"))
        .map_err(|error| io::Error::new(io::ErrorKind::NotFound, error))?;
    let start_identity = process_start_time(&stat)
        .ok_or_else(|| io::Error::other("process start identity was empty"))?;
    Ok(Some(ProcessIdentity {
        executable: executable.to_string_lossy().into_owned(),
        start_identity,
    }))
}

/// Field 22 of `/proc/<pid>/stat`: when the process started, in clock ticks.
///
/// Split from the read so it can be tested on any platform, and because the
/// parse has one trap in it. Field 2 is the command name in parentheses and may
/// itself contain spaces *and* parentheses -- a process is free to call itself
/// `my (weird) name` -- so splitting the line on whitespace mis-numbers every
/// field after it. Everything before the final `)` has to go first.
#[cfg(any(target_os = "linux", test))]
pub(crate) fn process_start_time(stat: &str) -> Option<String> {
    let after_comm = &stat[stat.rfind(')')? + 1..];
    // The first field after the comm is `state`, which is number 3.
    after_comm
        .split_whitespace()
        .nth(22 - 3)
        .filter(|ticks| !ticks.is_empty())
        .map(str::to_owned)
}

#[cfg(unix)]
pub(crate) fn process_identity(pid: u32) -> io::Result<ProcessIdentity> {
    let pid = pid.to_string();
    for attempt in 0..IDENTITY_SETTLE_ATTEMPTS {
        if let Some(identity) = query_process_identity(&pid)? {
            return Ok(identity);
        }
        if attempt + 1 < IDENTITY_SETTLE_ATTEMPTS {
            thread::sleep(IDENTITY_SETTLE_INTERVAL);
        }
    }
    Err(io::Error::new(
        io::ErrorKind::NotFound,
        "process never reported an executable path",
    ))
}

/// The identity of a child this daemon just spawned, waited for rather than
/// sampled a fixed number of times.
///
/// Two things end the window in which a fresh child has no path to report: it
/// finishes `exec`, or it exits. Both are observable, so both are what this
/// waits on. Counting samples instead meant a child that exited immediately
/// still cost the whole settle budget before reporting the one thing worth
/// knowing about it — that it had already died, and with what status.
#[cfg(unix)]
pub(crate) fn settled_process_identity(child: &mut Child) -> io::Result<ProcessIdentity> {
    let pid = child.id().to_string();
    // Read once, outside the loop: it cannot change, and it is a syscall.
    let own_image = std::env::current_exe().ok();
    let deadline = Instant::now() + IDENTITY_SETTLE_TIMEOUT;
    loop {
        match query_process_identity(&pid)? {
            Some(identity) if !names_our_own_image(&identity, own_image.as_deref()) => {
                return Ok(identity);
            }
            _ => {}
        }
        if child.try_wait()?.is_some() {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                "process exited before reporting an executable path",
            ));
        }
        if Instant::now() >= deadline {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                "process never reported an executable path",
            ));
        }
        thread::sleep(IDENTITY_SETTLE_INTERVAL);
    }
}

/// Whether a just-spawned child is still reporting *this* process's binary.
///
/// Between `fork` and `exec` a child is a copy of its parent, so on Linux
/// `/proc/<pid>/exe` names this daemon rather than the binary the child was
/// spawned to run -- there is no bracketed placeholder to give the window away,
/// only a plausible path that happens to be the wrong one. An identity read
/// there records the wrong executable, and the reclaim path declines to signal
/// a record whose executable no longer matches. So the leftover that record was
/// written to catch survives every future launch: exactly the failure the
/// ownership record exists to prevent, reached through the moment it is
/// written. It is how
/// `a_sidecar_that_outlived_its_daemon_is_reclaimed_before_the_next_spawn`
/// fails on a loaded Linux runner -- the leftover is never signalled and the
/// test waits out the full `sleep 30`.
///
/// This is the Linux counterpart of the `(sh)` that `ps` reports on macOS: both
/// mean "not settled yet", and both are waited out rather than recorded. A
/// daemon that genuinely spawned a copy of itself would wait out the whole
/// settle budget and then record anyway -- slower, and still correct.
#[cfg(unix)]
pub(crate) fn names_our_own_image(identity: &ProcessIdentity, own_image: Option<&Path>) -> bool {
    own_image.is_some_and(|own| Path::new(identity.executable.as_str()) == own)
}

/// Windows names a process's image at creation, so there is no window to wait
/// out and nothing a live child can report that a query would miss.
#[cfg(windows)]
pub(crate) fn settled_process_identity(child: &mut Child) -> io::Result<ProcessIdentity> {
    process_identity(child.id())
}

#[cfg(unix)]
pub(crate) fn terminate_verified_process(pid: u32) -> io::Result<()> {
    let pid = i32::try_from(pid).map_err(|_| io::Error::other("invalid process id"))?;
    // SAFETY: the caller has matched installation, executable and OS start identity.
    let result = unsafe { libc::kill(pid, libc::SIGTERM) };
    if result != 0 {
        let error = io::Error::last_os_error();
        if error.raw_os_error() == Some(libc::ESRCH) {
            return Ok(());
        }
        return Err(error);
    }
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        // SAFETY: signal zero only checks whether this exact PID still exists.
        if unsafe { libc::kill(pid, 0) } != 0 {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(50));
    }
    // SAFETY: identity was checked immediately before termination.
    unsafe { libc::kill(pid, libc::SIGKILL) };
    Ok(())
}

#[cfg(windows)]
pub(crate) fn process_identity(pid: u32) -> io::Result<ProcessIdentity> {
    use windows_sys::Win32::Foundation::{CloseHandle, FILETIME};
    use windows_sys::Win32::System::Threading::{
        GetProcessTimes, OpenProcess, QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION,
    };

    let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
    if handle.is_null() {
        return Err(io::Error::last_os_error());
    }
    let result = (|| {
        let mut path = vec![0_u16; 32_768];
        let mut path_len = path.len() as u32;
        if unsafe { QueryFullProcessImageNameW(handle, 0, path.as_mut_ptr(), &mut path_len) } == 0 {
            return Err(io::Error::last_os_error());
        }
        let executable = PathBuf::from(String::from_utf16_lossy(&path[..path_len as usize]))
            .canonicalize()?
            .to_string_lossy()
            .into_owned();
        let mut creation: FILETIME = unsafe { std::mem::zeroed() };
        let mut exit: FILETIME = unsafe { std::mem::zeroed() };
        let mut kernel: FILETIME = unsafe { std::mem::zeroed() };
        let mut user: FILETIME = unsafe { std::mem::zeroed() };
        if unsafe { GetProcessTimes(handle, &mut creation, &mut exit, &mut kernel, &mut user) } == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(ProcessIdentity {
            executable,
            start_identity: format!(
                "{:08x}{:08x}",
                creation.dwHighDateTime, creation.dwLowDateTime
            ),
        })
    })();
    unsafe { CloseHandle(handle) };
    result
}

#[cfg(windows)]
pub(crate) fn terminate_verified_process(pid: u32) -> io::Result<()> {
    use windows_sys::Win32::Foundation::CloseHandle;
    use windows_sys::Win32::System::Threading::{
        OpenProcess, TerminateProcess, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_TERMINATE,
    };
    let handle = unsafe {
        OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE,
            0,
            pid,
        )
    };
    if handle.is_null() {
        return Err(io::Error::last_os_error());
    }
    let result = if unsafe { TerminateProcess(handle, 1) } == 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    };
    unsafe { CloseHandle(handle) };
    result
}
