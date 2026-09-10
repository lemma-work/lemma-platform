//! The Virtualization.framework host: starting a VM, and reclaiming one
//! this installation left behind.

use super::*;

#[cfg(target_os = "macos")]
pub(crate) const VM_PROCESS_MARKER_SCHEMA_VERSION: u64 = 1;

#[cfg(target_os = "macos")]
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct VmProcessMarker {
    schema_version: u64,
    pid: u32,
    executable: String,
    start_identity: String,
}

#[cfg(target_os = "macos")]
pub(crate) struct ProcessIdentity {
    executable: String,
    start_identity: String,
}

#[cfg(target_os = "macos")]
pub(crate) fn process_identity(pid: u32) -> io::Result<ProcessIdentity> {
    let pid = pid.to_string();
    let executable = Command::new("/bin/ps")
        .args(["-p", &pid, "-o", "comm="])
        .output()?;
    let started = Command::new("/bin/ps")
        .args(["-p", &pid, "-o", "lstart="])
        .output()?;
    if !executable.status.success() || !started.status.success() {
        return Err(io::Error::new(io::ErrorKind::NotFound, "process not found"));
    }
    let executable = String::from_utf8(executable.stdout)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    let executable = Path::new(executable.trim())
        .canonicalize()?
        .to_string_lossy()
        .into_owned();
    let start_identity = String::from_utf8(started.stdout)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?
        .trim()
        .to_owned();
    if start_identity.is_empty() {
        return Err(io::Error::other("process start identity was empty"));
    }
    Ok(ProcessIdentity {
        executable,
        start_identity,
    })
}

#[cfg(target_os = "macos")]
pub(crate) fn terminate_verified_process(pid: u32) -> io::Result<()> {
    let pid = i32::try_from(pid).map_err(|_| io::Error::other("invalid process id"))?;
    // SAFETY: the caller matched the recorded executable and OS start identity.
    let result = unsafe { libc::kill(pid, libc::SIGTERM) };
    if result != 0 {
        let error = io::Error::last_os_error();
        if error.raw_os_error() == Some(libc::ESRCH) {
            return Ok(());
        }
        return Err(error);
    }
    let deadline = Instant::now() + Duration::from_secs(10);
    while Instant::now() < deadline {
        // SAFETY: signal zero only checks whether this exact PID still exists.
        if unsafe { libc::kill(pid, 0) } != 0 {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(50));
    }
    // SAFETY: identity was checked immediately before termination.
    if unsafe { libc::kill(pid, libc::SIGKILL) } != 0 {
        let error = io::Error::last_os_error();
        if error.raw_os_error() != Some(libc::ESRCH) {
            return Err(error);
        }
    }
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        // Wait for launchd to reap an orphaned helper. Starting a replacement
        // as soon as SIGKILL is delivered can race Virtualization.framework's
        // release of the exclusive data-disk attachment.
        if unsafe { libc::kill(pid, 0) } != 0 {
            thread::sleep(Duration::from_millis(500));
            return Ok(());
        }
        thread::sleep(Duration::from_millis(50));
    }
    Err(io::Error::new(
        io::ErrorKind::TimedOut,
        "terminated VM helper was not reaped",
    ))
}

impl ManagedRuntime {
    #[cfg(target_os = "macos")]
    pub(crate) fn start_macos(&self) -> io::Result<()> {
        let release = self.config.artifact_root.join("macos-aarch64");
        validate_macos_release(&release)?;
        let state = self.config.local_root.join("runtime/macos");
        fs::create_dir_all(&state)?;
        set_private_directory(&state)?;
        let mut guard = self.vm.lock().expect("VM lock poisoned");
        if let Some(child) = guard.as_mut() {
            if child.try_wait()?.is_none() {
                return Ok(());
            }
            remove_if_present(&self.vm_process_marker)?;
        }
        self.reclaim_owned_macos_vm()?;
        // Rewritten every boot, so the marker always describes *this* start
        // rather than some earlier one. A stale "fresh" marker is the one thing
        // that would let the guest format a disk holding user data.
        let disk_is_fresh = create_private_sparse_file(&state.join("data.raw"), DATA_DISK_BYTES)?;
        if disk_is_fresh {
            write_private_atomic(&self.data_disk_fresh_marker, b"1\n")?;
        } else {
            remove_if_present(&self.data_disk_fresh_marker)?;
        }
        remove_if_present(&self.control_socket)?;
        for port in [5432, 6379, 3567] {
            remove_if_present(&self.service_socket(port))?;
        }
        let log_path = self.config.local_root.join("logs/vz.log");
        rotate_log(&log_path, 5 * 1024 * 1024)?;
        // Unconditionally, not at 5 MiB. `guest_needs_data_repair` scans this
        // file for `lemma-data: needs-repair:` and the file is append-only, so
        // one bad boot condemned every boot after it -- including the boot that
        // follows a successful reset, which found the *old* line and offered the
        // same reset again. Forever, with only a full reinstall to escape.
        //
        // Kept as `.previous.log` rather than deleted: the run that failed is
        // exactly the one somebody wants to read, and it is one boot of history
        // either way.
        rotate_log(&state.join("console.log"), 0)?;
        let mut child = Command::new(&self.config.vz_executable)
            .arg("serve")
            .arg("--runtime")
            .arg(&state)
            .arg("--release")
            .arg(&release)
            .arg("--control-socket")
            .arg(&self.control_socket)
            .arg("--control-share")
            .arg(
                self.capability_file
                    .parent()
                    .ok_or_else(|| io::Error::other("capability parent is missing"))?,
            )
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::from(private_appending_log(&log_path)?))
            .spawn()?;
        if let Err(error) = self.record_macos_vm(&child) {
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
        *guard = Some(child);
        Ok(())
    }

    #[cfg(target_os = "macos")]
    pub(crate) fn record_macos_vm(&self, child: &Child) -> io::Result<()> {
        let identity = process_identity(child.id())?;
        let expected = self.config.vz_executable.canonicalize()?;
        if Path::new(&identity.executable).canonicalize()? != expected {
            return Err(io::Error::other(
                "VM helper executable did not match the app-owned runtime",
            ));
        }
        write_private_atomic(
            &self.vm_process_marker,
            &serde_json::to_vec_pretty(&VmProcessMarker {
                schema_version: VM_PROCESS_MARKER_SCHEMA_VERSION,
                pid: child.id(),
                executable: identity.executable,
                start_identity: identity.start_identity,
            })?,
        )
    }

    /// Terminate a VM helper this installation left behind, verified by
    /// identity rather than by name.
    ///
    /// Public so `lemma-locald reset` can reach it. That path runs when the
    /// daemon that owned the VM is already gone, so the marker on disk is the
    /// only way to find the helper -- and matching by name (`pkill -x
    /// lemma-vz`) would kill a developer's separate dev-root VM, or another
    /// installation's.
    #[cfg(target_os = "macos")]
    pub fn reclaim_owned_macos_vm(&self) -> io::Result<()> {
        self.reclaim_macos_vm(true)
    }

    /// Destructive recovery also handles a helper from a replaced app bundle.
    /// Its recorded executable and start identity still have to match the
    /// running process; its path need not match the newly installed binary.
    #[cfg(target_os = "macos")]
    pub fn reclaim_owned_macos_vm_for_reset(&self) -> io::Result<()> {
        self.reclaim_macos_vm(false)
    }

    #[cfg(target_os = "macos")]
    pub(crate) fn reclaim_macos_vm(&self, require_current_executable: bool) -> io::Result<()> {
        let raw = match fs::read(&self.vm_process_marker) {
            Ok(raw) if raw.len() <= 64 * 1024 => raw,
            Ok(_) => return Ok(()),
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
            Err(error) => return Err(error),
        };
        let Ok(marker) = serde_json::from_slice::<VmProcessMarker>(&raw) else {
            return Ok(());
        };
        if marker.schema_version != VM_PROCESS_MARKER_SCHEMA_VERSION {
            return Ok(());
        }
        let identity = match process_identity(marker.pid) {
            Ok(identity) => identity,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                return remove_if_present(&self.vm_process_marker);
            }
            Err(error) => return Err(error),
        };
        if identity.executable == marker.executable
            && identity.start_identity == marker.start_identity
        {
            if require_current_executable {
                let expected = self.config.vz_executable.canonicalize()?;
                if !Path::new(&identity.executable)
                    .canonicalize()
                    .is_ok_and(|actual| actual == expected)
                {
                    return Err(io::Error::other("the running VM belongs to a different app release; use confirmed installation cleanup"));
                }
            }
            terminate_verified_process(marker.pid)?;
        }
        remove_if_present(&self.vm_process_marker)
    }

    #[cfg(target_os = "macos")]
    pub(crate) fn macos_exit_error(&self) -> io::Result<Option<io::Error>> {
        let mut guard = self.vm.lock().expect("VM lock poisoned");
        let Some(child) = guard.as_mut() else {
            return Ok(None);
        };
        let Some(status) = child.try_wait()? else {
            return Ok(None);
        };
        *guard = None;
        remove_if_present(&self.vm_process_marker)?;
        let log = fs::read(self.config.local_root.join("logs/vz.log")).unwrap_or_default();
        let detail = last_diagnostic(&log, "the runtime log holds no explanation");
        Ok(Some(io::Error::other(format!(
            "Lemma's private runtime exited ({status}): {detail}"
        ))))
    }
}
