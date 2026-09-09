//! Starting the runtime, waiting for it, and stopping it again.

use super::*;

impl ManagedRuntime {
    pub fn start(&self) -> io::Result<ManagedRuntimeStatus> {
        self.ensure_capability()?;
        #[cfg(target_os = "macos")]
        {
            self.refresh_host_epoch()?;
            self.start_macos()?;
        }
        #[cfg(windows)]
        self.start_windows()?;
        self.wait_ready()
    }

    pub fn prepare_host(&self) -> io::Result<Value> {
        #[cfg(target_os = "macos")]
        {
            Ok(json!({"ready": true, "reboot_required": false, "platform": "macos"}))
        }
        #[cfg(windows)]
        {
            self.prepare_windows_host()
        }
        #[cfg(not(any(target_os = "macos", windows)))]
        {
            Err(io::Error::new(
                io::ErrorKind::Unsupported,
                "managed host preparation is unsupported on this platform",
            ))
        }
    }

    /// Verify both the platform runtime process and the guest control plane.
    ///
    /// This is intentionally stronger than checking whether the last start
    /// succeeded: the VM or WSL distribution may disappear while the native
    /// backend and frontend processes remain alive.
    pub fn health(&self) -> io::Result<ManagedRuntimeStatus> {
        #[cfg(target_os = "macos")]
        self.check_guest_kernel()?;
        #[cfg(target_os = "macos")]
        if let Some(error) = self.macos_exit_error()? {
            return Err(error);
        }
        let result = match self.request("health", json!({})) {
            Ok(result) => result,
            Err(error) => {
                // A torn containerd cache is disposable, but it must be reset
                // while the guest is offline. The guest persists a reset
                // marker; a clean stop lets the next boot repair only that
                // cache while preserving named volumes and workspaces.
                if cache_repair_required(&error) {
                    let _ = self.stop();
                }
                return Err(error);
            }
        };
        serde_json::from_value(result).map_err(|error| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                format!("invalid guest health response: {error}"),
            )
        })
    }

    /// Discard the guest's data disk entirely, returning the bytes reclaimed.
    ///
    /// The blunt half of a local-data reset, for when the guest cannot be asked
    /// to tidy up after itself: a torn filesystem, a VM that will not boot, a
    /// disk whose size no longer matches. It takes the pulled container images
    /// with it, so `core.reset_data` inside the guest is preferred wherever the
    /// guest still answers.
    ///
    /// Stop first, then reclaim. `stop` handles the VM this process owns;
    /// `reclaim_owned_macos_vm` is the second pass for a helper left behind by
    /// a daemon that died without stopping it, and it verifies pid, executable
    /// and start identity before signalling anything. Unlinking a disk another
    /// process still has attached is the one thing that must not happen here.
    #[cfg(target_os = "macos")]
    pub fn discard_data_disk(&self) -> io::Result<u64> {
        self.stop()?;
        self.reclaim_owned_macos_vm()?;

        let disk = self.config.local_root.join("runtime/macos/data.raw");
        // Allocated blocks, not `len()`. The file is sparse and always reports
        // 24 GiB apparent size, so reporting `len()` would tell every user they
        // just recovered 24 GiB regardless of what was actually on it.
        let reclaimed = disk
            .metadata()
            .map(|metadata| {
                use std::os::unix::fs::MetadataExt;
                metadata.blocks() * 512
            })
            .unwrap_or(0);

        // Removed, never truncated. `create_private_sparse_file` refuses a file
        // whose length is not exactly `DATA_DISK_BYTES`, so a `set_len(0)` here
        // would leave the installation permanently unable to start with
        // "managed data disk has an unexpected size".
        remove_if_present(&disk)?;
        remove_if_present(&self.control_socket)?;
        Ok(reclaimed)
    }

    pub fn stop(&self) -> io::Result<()> {
        #[cfg(target_os = "macos")]
        {
            if let Some(mut child) = self.vm.lock().expect("VM lock poisoned").take() {
                let _ = self.request("system.shutdown", json!({}));
                let deadline = Instant::now() + Duration::from_secs(20);
                while Instant::now() < deadline {
                    if child.try_wait()?.is_some() {
                        remove_if_present(&self.vm_process_marker)?;
                        return Ok(());
                    }
                    thread::sleep(Duration::from_millis(100));
                }
                let result = unsafe { libc::kill(child.id() as libc::pid_t, libc::SIGTERM) };
                if result != 0 {
                    return Err(io::Error::last_os_error());
                }
                let deadline = Instant::now() + Duration::from_secs(5);
                while Instant::now() < deadline {
                    if child.try_wait()?.is_some() {
                        remove_if_present(&self.vm_process_marker)?;
                        return Ok(());
                    }
                    thread::sleep(Duration::from_millis(100));
                }
                child.kill()?;
                child.wait()?;
                remove_if_present(&self.vm_process_marker)?;
            } else {
                self.reclaim_owned_macos_vm()?;
            }
        }
        #[cfg(windows)]
        {
            // WSL's private distribution is terminated by the host rather
            // than systemd poweroff. Ask the guest to stop every managed
            // container first so databases and sandboxes flush cleanly.
            let _ = self.request("system.shutdown", json!({}));
            // Through the shared runner rather than its own `.output()`. This
            // was a second unbounded wait, on the path where a hang is most
            // visible to a person: they are watching a window refuse to close.
            self.wsl(&["--terminate", self.wsl_distribution()], None)?;
        }
        Ok(())
    }

    pub fn capability_file(&self) -> &Path {
        &self.capability_file
    }

    pub fn control_socket(&self) -> &Path {
        &self.control_socket
    }

    #[cfg(target_os = "macos")]
    pub fn service_socket(&self, port: u16) -> PathBuf {
        self.config
            .local_root
            .join(format!("run/service-{port}.sock"))
    }

    pub(crate) fn ensure_capability(&self) -> io::Result<()> {
        if self.capability_file.is_file() {
            let current = fs::read_to_string(&self.capability_file)?;
            if current.trim().len() == CAPABILITY_BYTES * 2 {
                return ensure_private_file(&self.capability_file);
            }
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "managed guest capability is corrupt",
            ));
        }
        let mut bytes = [0_u8; CAPABILITY_BYTES];
        getrandom::fill(&mut bytes)
            .map_err(|error| io::Error::other(format!("secure randomness failed: {error}")))?;
        let value: String = bytes.iter().map(|byte| format!("{byte:02x}")).collect();
        write_private_atomic(&self.capability_file, value.as_bytes())
    }

    /// Whether the guest reported its data disk as unmountable, and why.
    ///
    /// Read from the serial console, which the guest writes to before anything
    /// it could report over is running -- `lemma-guestd` requires the mount
    /// that just failed. That makes the console the only channel available for
    /// this class of failure, and it is already a Diagnostics source.
    #[cfg(target_os = "macos")]
    /// Only this boot's console is consulted -- see the rotation in `start`,
    /// which is what makes that true.
    pub(crate) fn guest_needs_data_repair(&self) -> Option<String> {
        const MARKER: &str = "lemma-data: needs-repair:";
        let console = self.config.local_root.join("runtime/macos/console.log");
        let text = fs::read_to_string(console).ok()?;
        text.lines()
            .rev()
            .find_map(|line| line.split_once(MARKER))
            .map(|(_, reason)| reason.trim().to_owned())
    }

    pub fn check_guest_kernel(&self) -> io::Result<()> {
        #[cfg(target_os = "macos")]
        kernel_health::check_console(&self.config.local_root.join("runtime/macos/console.log"))?;
        Ok(())
    }

    pub(crate) fn wait_ready(&self) -> io::Result<ManagedRuntimeStatus> {
        let deadline = Instant::now() + Duration::from_secs(120);
        let mut last_error = None;
        while Instant::now() < deadline {
            #[cfg(target_os = "macos")]
            self.check_guest_kernel()?;
            #[cfg(target_os = "macos")]
            if let Some(error) = self.macos_exit_error()? {
                return Err(error);
            }
            // A guest that has decided its data disk needs repair will never
            // answer: `lemma-data.service` failed, and `lemma-guestd.service`
            // requires it. Waiting out the remaining budget would turn a known,
            // named problem into "did not become ready".
            #[cfg(target_os = "macos")]
            if let Some(reason) = self.guest_needs_data_repair() {
                return Err(io::Error::other(format!(
                    "Lemma's private data disk needs repair: {reason}; {DATA_RESET_MARKER}"
                )));
            }
            match self.health() {
                Ok(status) => return Ok(status),
                Err(error) => last_error = Some(error),
            }
            thread::sleep(Duration::from_millis(250));
        }
        Err(last_error.unwrap_or_else(|| {
            io::Error::new(
                io::ErrorKind::TimedOut,
                "managed guest did not become ready",
            )
        }))
    }
}
