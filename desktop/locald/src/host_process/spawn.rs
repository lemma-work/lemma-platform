//! Starting one service, and stopping it without orphaning its children.

use super::*;

pub(crate) fn random_generation() -> io::Result<String> {
    let mut bytes = [0_u8; 16];
    getrandom::fill(&mut bytes)
        .map_err(|error| io::Error::other(format!("runtime generation failed: {error}")))?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

pub(crate) fn spawn_process(spec: &HostProcessSpec, log_dir: &Path) -> io::Result<Child> {
    spawn_command(
        &spec.command,
        spec.cwd.as_deref(),
        &spec.env,
        process_log(log_dir, &spec.id)?,
    )
}

pub(crate) fn spawn_command(
    arguments: &[String],
    cwd: Option<&Path>,
    environment: &HashMap<String, String>,
    stdout: File,
) -> io::Result<Child> {
    let stderr = stdout.try_clone()?;
    let mut command = Command::new(&arguments[0]);
    command
        .args(&arguments[1..])
        .env_clear()
        // Services opt into an EOF watchdog. Keeping this pipe owned by Child
        // makes an abrupt locald exit observable without inspecting or killing
        // unrelated system processes on the next launch.
        .stdin(Stdio::piped())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr));
    for key in INHERITED_ENVIRONMENT {
        if let Some(value) = std::env::var_os(key) {
            command.env(key, value);
        }
    }
    command.envs(environment);
    if let Some(cwd) = cwd {
        command.current_dir(cwd);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // CREATE_NEW_PROCESS_GROUP so the group can be signalled as a unit --
        // the Windows provider upgrades this to a Job Object before host packs
        // become the default -- plus CREATE_NO_WINDOW, because these are
        // console programs (python.exe, node.exe) started by a GUI app with no
        // console and each would otherwise be given a conhost window.
        //
        // Spelled out together because creation_flags replaces the flag set.
        command.creation_flags(crate::CREATE_NO_WINDOW | crate::CREATE_NEW_PROCESS_GROUP);
    }
    command.spawn()
}

#[cfg(windows)]
pub(crate) fn create_windows_job() -> io::Result<usize> {
    use windows_sys::Win32::System::JobObjects::{
        CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
    if handle.is_null() {
        return Err(io::Error::last_os_error());
    }
    let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    let configured = unsafe {
        SetInformationJobObject(
            handle,
            JobObjectExtendedLimitInformation,
            &limits as *const _ as *const _,
            std::mem::size_of_val(&limits) as u32,
        )
    };
    if configured == 0 {
        unsafe { windows_sys::Win32::Foundation::CloseHandle(handle) };
        return Err(io::Error::last_os_error());
    }
    Ok(handle as usize)
}

#[cfg(windows)]
pub(crate) fn assign_child_to_windows_job(job: usize, child: &mut Child) -> io::Result<()> {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::System::JobObjects::AssignProcessToJobObject;
    let assigned = unsafe { AssignProcessToJobObject(job as _, child.as_raw_handle() as _) };
    if assigned == 0 {
        let error = io::Error::last_os_error();
        let _ = child.kill();
        let _ = child.wait();
        Err(error)
    } else {
        Ok(())
    }
}

#[cfg(unix)]
pub(crate) fn terminate_process_group(child: &mut Child) -> io::Result<()> {
    // Reap first. A child that has already exited still reports its old pid,
    // and on a busy machine that number gets recycled quickly -- so signalling
    // `-pid` here would reach whatever process group inherited it, which is at
    // best somebody else's processes and at worst our own unrelated services.
    // There is nothing to terminate in that case anyway.
    if child.try_wait()?.is_some() {
        return Ok(());
    }
    let process_group = -(child.id() as i32);
    // SAFETY: kill is called with a process group created for this exact child.
    let result = unsafe { libc::kill(process_group, libc::SIGTERM) };
    if result != 0 {
        let error = io::Error::last_os_error();
        // ESRCH means the group went away between the check above and the
        // signal. EPERM means the id now names processes that are not ours,
        // which says the same thing: ours are gone. Neither is a reason to
        // fail the stop -- doing so aborted `stop_all` partway through and
        // left the rest of the stack running.
        if !matches!(error.raw_os_error(), Some(libc::ESRCH) | Some(libc::EPERM)) {
            return Err(error);
        }
    }
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        if child.try_wait()?.is_some() {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(50));
    }
    // SAFETY: same owned process group, now beyond graceful timeout.
    unsafe { libc::kill(process_group, libc::SIGKILL) };
    child.wait().map(|_| ())
}

#[cfg(windows)]
pub(crate) fn terminate_process_group(child: &mut Child) -> io::Result<()> {
    // This was `child.kill()`, which is TerminateProcess: no chance to flush,
    // no shutdown hook, and it does not touch descendants. The backend lost
    // in-flight writes and left Postgres with unclean disconnects on every
    // stop, and anything it or Next.js had spawned kept running -- and kept its
    // port -- until locald itself exited.
    //
    // Windows has no SIGTERM, and CREATE_NO_WINDOW gives each child its own
    // console, so GenerateConsoleCtrlEvent cannot reach them from here. What
    // does exist is the EOF watchdog services opt into: dropping our end of
    // their stdin closes the pipe, which is the same shutdown signal an abrupt
    // locald exit gives them.
    drop(child.stdin.take());
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        if child.try_wait()?.is_some() {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(50));
    }
    // Beyond the graceful window. Take the tree, not just the child, so nothing
    // is left holding a port the next start needs.
    let felled = Command::new("taskkill")
        .no_console_window()
        .args(["/PID", &child.id().to_string(), "/T", "/F"])
        .status()
        .is_ok_and(|status| status.success());
    if !felled {
        child.kill()?;
    }
    child.wait().map(|_| ())
}

impl HostProcessManager {
    pub(crate) fn spawn_if_missing(&self, id: &str) -> io::Result<()> {
        {
            let mut state = self.state.lock().expect("host process lock poisoned");
            if let Some(child) = state.children.get_mut(id) {
                if child.child.try_wait()?.is_none() {
                    return Ok(());
                }
            }
            state.children.remove(id);
        }

        let spec = self.process_spec_for_spawn(id)?;
        let mut child = spawn_process(&spec, &self.log_dir)?;
        #[cfg(windows)]
        assign_child_to_windows_job(self.windows_job, &mut child)?;
        if let Err(error) = self.record_child(id, &mut child) {
            if let Some(status) = child.try_wait()? {
                let excerpt = tail_log(&self.log_dir.join(format!("{id}.log")), 8 * 1024);
                let suffix = excerpt
                    .filter(|value| !value.trim().is_empty())
                    .map(|value| format!("; recent log:\n{}", self.redact_excerpt(value)))
                    .unwrap_or_default();
                return Err(io::Error::other(format!(
                    "{id} process exited with {status}{suffix}"
                )));
            }
            let _ = terminate_process_group(&mut child);
            return Err(io::Error::other(format!(
                "could not record ownership of {id}: {error}"
            )));
        }
        self.state
            .lock()
            .expect("host process lock poisoned")
            .children
            .insert(
                id.to_owned(),
                ManagedChild {
                    child,
                    started_at: Instant::now(),
                },
            );
        Ok(())
    }

    pub(crate) fn process_spec_for_spawn(&self, id: &str) -> io::Result<HostProcessSpec> {
        let mut spec = self
            .by_id
            .get(id)
            .cloned()
            .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, id.to_owned()))?;
        if id == "backend" {
            spec.env.extend(
                self.backend_environment
                    .lock()
                    .expect("backend environment lock poisoned")
                    .clone(),
            );
        }
        spec.env.extend(self.service_environment(id));
        let generation = self
            .runtime_generation
            .lock()
            .expect("runtime generation lock poisoned")
            .clone();
        if !generation.is_empty() {
            match id {
                "backend" => {
                    spec.env
                        .insert("LEMMA_RUNTIME_INSTANCE_ID".into(), generation.clone());
                }
                "frontend" => {
                    spec.env.insert(
                        "NEXT_PUBLIC_LEMMA_RUNTIME_INSTANCE_ID".into(),
                        generation.clone(),
                    );
                }
                _ => {}
            }
            if let Some(health) = spec.health.as_mut() {
                health.expected_body = Some(generation);
            }
        }
        Ok(spec)
    }

    pub(crate) fn health_spec(&self, id: &str) -> Option<HttpHealthSpec> {
        let mut health = self.by_id.get(id)?.health.clone()?;
        let generation = self
            .runtime_generation
            .lock()
            .expect("runtime generation lock poisoned")
            .clone();
        if !generation.is_empty() {
            health.expected_body = Some(generation);
        }
        Some(health)
    }

    pub(crate) fn stop_process(&self, id: &str) -> io::Result<()> {
        let child = self
            .state
            .lock()
            .expect("host process lock poisoned")
            .children
            .remove(id);
        let result = match child {
            Some(mut child) => terminate_process_group(&mut child.child),
            None => Ok(()),
        };
        if result.is_ok() {
            self.remove_ledger_entry(id)?;
        }
        result
    }
}
