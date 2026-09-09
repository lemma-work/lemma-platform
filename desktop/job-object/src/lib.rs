//! A Windows job object, so a process tree dies with the thing that started it.
//!
//! On unix a process group and one signal end an agent and everything it
//! started. Windows has no equivalent. `taskkill /T` walks the tree the OS
//! still maintains, which misses exactly the case that matters here: agents
//! ship behind wrapper launchers -- `npx`, `uvx` -- and a wrapper that has
//! already exited leaves its child in no tree at all. That child holds the
//! workspace open, holds the provider credential, and can still write files,
//! after the run it belonged to has ended.
//!
//! A job object does not have that hole. Every process put in one, and
//! everything they go on to start, belongs to it for good, and closing the last
//! handle with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` ends all of them.
//!
//! Its own crate because of one word. `lemma-desktop-process` is where this
//! belongs by subject and `lemma-agent-host` is where it is needed, and both
//! declare `unsafe_code = "forbid"` -- which cannot be lifted locally, and is
//! not a policy worth weakening across a whole crate for four calls into
//! `Win32`. So the `unsafe` is here, in a crate small enough to read in one
//! sitting, and nothing else changes its mind about unsafe code.

#![cfg_attr(not(windows), allow(unused))]

use std::io;

/// A job object that kills everything in it when it is dropped.
///
/// On platforms without job objects this is an empty handle that does nothing,
/// so a caller needs no `cfg` of its own: the guarantee is Windows-only
/// because the problem is.
pub struct Job {
    #[cfg(windows)]
    handle: isize,
}

impl Job {
    /// Create a job whose processes die when this value is dropped.
    #[cfg(windows)]
    pub fn new() -> io::Result<Self> {
        use windows_sys::Win32::Foundation::CloseHandle;
        use windows_sys::Win32::System::JobObjects::{
            CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        };

        // SAFETY: a null name and null attributes are the documented way to
        // create an unnamed job, and the returned handle is checked.
        let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if handle.is_null() {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: an all-zero `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` is the
        // documented starting point; only the flags are then set.
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        // SAFETY: `limits` outlives the call, and the length passed is its own.
        let configured = unsafe {
            SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                std::ptr::from_ref(&limits).cast(),
                u32::try_from(std::mem::size_of_val(&limits)).unwrap_or(0),
            )
        };
        if configured == 0 {
            let error = io::Error::last_os_error();
            // SAFETY: the handle was just created here and is not used again.
            unsafe { CloseHandle(handle) };
            return Err(error);
        }
        Ok(Self {
            handle: handle as isize,
        })
    }

    /// Everywhere else, a job that holds nothing and promises nothing.
    #[cfg(not(windows))]
    pub fn new() -> io::Result<Self> {
        Ok(Self {})
    }

    /// Put a process, and everything it goes on to start, in this job.
    ///
    /// A freshly spawned child is never already in a job that forbids this.
    #[cfg(windows)]
    pub fn adopt(&self, process: std::os::windows::io::RawHandle) -> io::Result<()> {
        use windows_sys::Win32::System::JobObjects::AssignProcessToJobObject;

        // SAFETY: both handles are owned by their holders for the call.
        let assigned = unsafe { AssignProcessToJobObject(self.handle as *mut _, process.cast()) };
        if assigned == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }
}

impl Drop for Job {
    /// Implemented on every platform, even where it does nothing.
    ///
    /// The type's whole contract is "dropping this ends what is in it", and a
    /// caller writing `drop(job)` should not have to know which platform gives
    /// that meaning -- nor be told by a lint that the value it is dropping has
    /// no `Drop`.
    fn drop(&mut self) {
        #[cfg(windows)]
        {
            use windows_sys::Win32::Foundation::CloseHandle;

            // Closing the last handle is what ends the job's processes. There
            // is nothing useful to do with a failure: the handle goes away
            // with this process either way.
            // SAFETY: the handle came from `CreateJobObjectW` and is closed
            // exactly once, here.
            unsafe { CloseHandle(self.handle as *mut _) };
        }
    }
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    use std::os::windows::io::AsRawHandle;
    use std::process::{Command, Stdio};
    use std::time::{Duration, Instant};

    /// The case `taskkill /T` cannot reach.
    ///
    /// A wrapper that starts a grandchild and exits leaves that grandchild in
    /// no tree at all -- which is the normal shape behind `npx` and `uvx`, and
    /// exactly how an agent survived the run it belonged to. Closing the job
    /// ends it anyway.
    #[test]
    fn a_grandchild_whose_parent_has_gone_still_dies_with_the_job() {
        // By process id, not by image name. `ping.exe` is a name anything on
        // the machine may be using -- a runner builds several jobs at once --
        // so an image-name check could watch somebody else's process and
        // report either answer for the wrong reason.
        let pings = || -> std::collections::BTreeSet<String> {
            let output = Command::new("tasklist")
                .args(["/FI", "IMAGENAME eq ping.exe", "/FO", "CSV", "/NH"])
                .output()
                .expect("tasklist runs");
            String::from_utf8_lossy(&output.stdout)
                .lines()
                .filter_map(|line| line.split(',').nth(1))
                .map(|field| field.trim().trim_matches('"').to_owned())
                .filter(|pid| pid.chars().all(|digit| digit.is_ascii_digit()) && !pid.is_empty())
                .collect()
        };
        let before = pings();

        let job = Job::new().expect("a job object");
        // `cmd` starts a detached `ping` that outlives it, then exits. `ping
        // -t` runs until something kills it.
        let mut wrapper = Command::new("cmd")
            .args(["/c", "start", "/b", "ping", "-t", "127.0.0.1"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("the wrapper starts");
        job.adopt(wrapper.as_raw_handle())
            .expect("the wrapper joins the job");
        wrapper.wait().expect("the wrapper exits on its own");

        // Waited for, not sampled. `cmd` exits as soon as it has asked for the
        // grandchild; the grandchild is not in the process list at that
        // instant, and checking once there failed the assertion that is
        // supposed to establish this test means anything.
        let deadline = Instant::now() + Duration::from_secs(10);
        let grandchild = loop {
            if let Some(pid) = pings().difference(&before).next().cloned() {
                break pid;
            }
            assert!(
                Instant::now() < deadline,
                "the grandchild never appeared, so there is nothing here to outlive its parent"
            );
            std::thread::sleep(Duration::from_millis(100));
        };

        let alive = |pid: &str| {
            let output = Command::new("tasklist")
                .args(["/FI", &format!("PID eq {pid}"), "/FO", "CSV", "/NH"])
                .output()
                .expect("tasklist runs");
            String::from_utf8_lossy(&output.stdout).contains(pid)
        };
        assert!(
            alive(&grandchild),
            "the grandchild outlived its parent, as it must for this test to mean anything"
        );

        drop(job);

        let deadline = Instant::now() + Duration::from_secs(10);
        while Instant::now() < deadline {
            if !alive(&grandchild) {
                return;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        // Best effort cleanup before failing, so a broken run does not leave a
        // `ping -t` behind on the runner for ever.
        let _ = Command::new("taskkill")
            .args(["/F", "/PID", &grandchild])
            .output();
        panic!("closing the job left the grandchild running");
    }
}
