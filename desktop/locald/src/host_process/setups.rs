//! One-time work before the services start, and the stamp that says it
//! is done.

use super::*;

/// Whether a stamped setup has already been done, for this exact stamp.
///
/// A free function so it can be asserted on every platform. The tests that
/// exercise it end to end have to spawn `/bin/sh`, so they are `#[cfg(unix)]` --
/// and gating them left Windows covering none of this, which is the wrong trade
/// for a decision that is pure and is the whole point of the feature.
///
/// No stamp means always run: that is how a setup opts out, and it is what
/// everything did before stamps existed. A stamp that differs from the recorded
/// one means the work is not the work that was done -- a new pack release, or
/// migrations that changed within one.
pub(crate) fn setup_is_already_done(stamp: Option<&str>, recorded: Option<&String>) -> bool {
    match stamp {
        None => false,
        Some(stamp) => recorded.map(String::as_str) == Some(stamp),
    }
}

pub(crate) fn wait_for_setup_dependency(
    setup: &HostSetupSpec,
    environment: &HashMap<String, String>,
    setup_deadline: Instant,
) -> io::Result<()> {
    if setup.id != "migrations" {
        return Ok(());
    }
    let Some(address) = environment
        .get("DATABASE_URL")
        .and_then(|url| database_socket_address(url))
    else {
        return Ok(());
    };

    // The VM readiness check runs before host setup, but a newly established
    // macOS route can still flap during the few milliseconds before asyncpg
    // opens its first connection. Gate every Alembic attempt on the exact
    // database endpoint it will use. Keep this bounded so a broken route
    // produces an actionable error instead of an apparent startup hang.
    let deadline = std::cmp::min(setup_deadline, Instant::now() + Duration::from_secs(30));
    loop {
        let error = match TcpStream::connect_timeout(&address, Duration::from_millis(750)) {
            Ok(_) => return Ok(()),
            Err(error) => error,
        };
        if Instant::now() >= deadline {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                setup_dependency_error(address, &error),
            ));
        }
        thread::sleep(Duration::from_millis(250));
    }
}

pub(crate) fn setup_dependency_error(address: SocketAddr, error: &io::Error) -> String {
    #[cfg(target_os = "macos")]
    if !address.ip().is_loopback()
        && matches!(
            error.raw_os_error(),
            Some(libc::EHOSTUNREACH | libc::ENETUNREACH | libc::EACCES | libc::EPERM)
        )
    {
        // EHOSTUNREACH can mean either a missing route or macOS privacy denial.
        // Terminal probes are exempt from local network privacy and cannot
        // establish whether the app has permission.
        return format!(
            "Lemma cannot connect to its local services. In System Settings > Privacy & Security > \
             Local Network, check that Lemma is allowed, then return here and choose Try again. \
             macOS requires this access to reach Lemma's private virtual machine on this Mac. \
             If access is already allowed, restart Lemma and check any VPN or firewall rules. \
             Your local data is preserved; a factory reset is not needed for this connection error. \
             Connection details: PostgreSQL at {address}: {error}"
        );
    }
    format!(
        "Lemma could not reach its local database before setup. Try again; if this continues, \
         restart Lemma and view the runtime log. Your local data is preserved. \
         Connection details: PostgreSQL at {address}: {error}"
    )
}

pub(crate) fn database_socket_address(url: &str) -> Option<SocketAddr> {
    let (_, remainder) = url.split_once("://")?;
    let authority = remainder.split(['/', '?', '#']).next().unwrap_or_default();
    let host_and_port = authority.rsplit('@').next().unwrap_or(authority);
    let (host, port) = if let Some(bracketed) = host_and_port.strip_prefix('[') {
        let (host, suffix) = bracketed.split_once(']')?;
        let port = suffix.strip_prefix(':')?.parse::<u16>().ok()?;
        (host, port)
    } else if let Some((host, port)) = host_and_port.rsplit_once(':') {
        (host, port.parse::<u16>().ok()?)
    } else {
        (host_and_port, 5432)
    };
    let ip = host.parse::<IpAddr>().ok()?;
    Some(SocketAddr::new(ip, port))
}

impl HostProcessManager {
    /// Whether a failed setup should stop the start, or only be recorded.
    ///
    /// Hoisted out of `run_setups` because `optional` used to be honoured at
    /// exactly one of the three places a setup can fail. A setup that *exited*
    /// non-zero was tolerated; the same setup *hanging*, or running out of
    /// budget before its next retry, took the whole stack down -- the reverse of
    /// what `optional` means. `connector-catalog` is declared optional with a
    /// 600-second timeout precisely so an unreachable third-party catalog cannot
    /// stop a workspace, and a blackholed route defeated that.
    ///
    /// Returns the error to raise, or `None` when the caller should carry on to
    /// the next setup. A function rather than a closure because the caller has
    /// to `continue 'setups`, which a closure cannot do.
    pub(crate) fn optional_setup_outcome(
        setup: &HostSetupSpec,
        detail: String,
    ) -> Option<io::Error> {
        if setup.optional {
            // Logged rather than raised: the log line is the record, and the
            // stack still comes up.
            eprintln!("locald: {detail}");
            return None;
        }
        Some(io::Error::other(detail))
    }

    /// Where completed setup stamps live, beside the process ledger.
    ///
    /// Under the locald root on purpose: a local-data reset removes that whole
    /// directory, so a wiped database can never be left with a stamp claiming
    /// its migrations have already run.
    pub(crate) fn setup_stamp_path(&self) -> PathBuf {
        self.log_dir
            .parent()
            .unwrap_or(&self.log_dir)
            .join("setup-stamps.json")
    }

    /// Forget every completed setup, so the next start runs them all again.
    ///
    /// A local-data reset destroys the database the migrations stamp describes
    /// but leaves the locald root standing -- so without this the next start
    /// would skip migrations against an empty schema and the backend would come
    /// up against tables that do not exist. The full reinstall removes the root
    /// entirely and takes the stamps with it.
    pub fn forget_setup_stamps(&self) -> io::Result<()> {
        match fs::remove_file(self.setup_stamp_path()) {
            Err(error) if error.kind() != io::ErrorKind::NotFound => Err(error),
            _ => Ok(()),
        }
    }

    pub(crate) fn recorded_setup_stamps(&self) -> HashMap<String, String> {
        std::fs::read(self.setup_stamp_path())
            .ok()
            .and_then(|raw| serde_json::from_slice(&raw).ok())
            .unwrap_or_default()
    }

    /// Record a setup as done for this stamp. Written only after it succeeded.
    pub(crate) fn record_setup_stamp(&self, id: &str, stamp: &str) {
        let mut stamps = self.recorded_setup_stamps();
        stamps.insert(id.to_owned(), stamp.to_owned());
        // Best effort: a stamp that cannot be written costs the next start the
        // work again, which is exactly the behaviour before stamps existed.
        if let Ok(encoded) = serde_json::to_vec_pretty(&stamps) {
            let _ = write_private_atomic(&self.setup_stamp_path(), &encoded);
        }
    }

    pub(crate) fn run_setups(&self) -> io::Result<()> {
        let recorded = self.recorded_setup_stamps();
        'setups: for setup in &self.manifest.setup {
            if setup_is_already_done(setup.stamp.as_deref(), recorded.get(&setup.id)) {
                continue 'setups;
            }
            let mut environment = setup.env.clone();
            environment.extend(
                self.backend_environment
                    .lock()
                    .expect("backend environment lock poisoned")
                    .clone(),
            );
            environment.extend(self.service_environment("backend"));
            let deadline = Instant::now() + Duration::from_secs(setup.timeout_seconds);
            for attempt in 1..=setup.max_attempts {
                wait_for_setup_dependency(setup, &environment, deadline)?;
                let mut child = spawn_command(
                    &setup.command,
                    setup.cwd.as_deref(),
                    &environment,
                    process_log(&self.log_dir, &setup.id)?,
                )?;
                #[cfg(windows)]
                assign_child_to_windows_job(self.windows_job, &mut child)?;
                loop {
                    if let Some(status) = child.try_wait()? {
                        if status.success() {
                            // Only here. A stamp written anywhere else would
                            // let a failed or half-finished setup be skipped on
                            // the next start, which is worse than running it
                            // again.
                            if let Some(stamp) = setup.stamp.as_deref() {
                                self.record_setup_stamp(&setup.id, stamp);
                            }
                            continue 'setups;
                        }
                        if attempt == setup.max_attempts {
                            let detail = format!(
                                "{} setup exited with {status} after {attempt} attempts; see {}",
                                setup.id,
                                self.log_dir.join(format!("{}.log", setup.id)).display()
                            );
                            match Self::optional_setup_outcome(setup, detail) {
                                None => continue 'setups,
                                Some(error) => return Err(error),
                            }
                        }
                        let backoff = Duration::from_secs(
                            setup.retry_backoff_seconds.saturating_mul(attempt as u64),
                        );
                        if Instant::now() + backoff >= deadline {
                            let detail = format!(
                                "{} setup exited with {status}; see {}",
                                setup.id,
                                self.log_dir.join(format!("{}.log", setup.id)).display()
                            );
                            match Self::optional_setup_outcome(setup, detail) {
                                None => continue 'setups,
                                Some(error) => return Err(error),
                            }
                        }
                        writeln!(
                            process_log(&self.log_dir, &setup.id)?,
                            "lemma-locald: setup attempt {attempt} exited with {status}; retrying"
                        )?;
                        thread::sleep(backoff);
                        break;
                    }
                    if Instant::now() >= deadline {
                        // Terminate first, on both paths. An optional setup that
                        // hangs and is then tolerated would otherwise be left
                        // running as an orphan holding a copy of the backend
                        // environment -- Postgres and Redis passwords included.
                        let _ = terminate_process_group(&mut child);
                        let detail = format!(
                            "{} setup exceeded {} seconds; see {}",
                            setup.id,
                            setup.timeout_seconds,
                            self.log_dir.join(format!("{}.log", setup.id)).display()
                        );
                        match Self::optional_setup_outcome(setup, detail) {
                            None => continue 'setups,
                            Some(error) => {
                                return Err(io::Error::new(io::ErrorKind::TimedOut, error))
                            }
                        }
                    }
                    thread::sleep(Duration::from_millis(50));
                }
            }
        }
        Ok(())
    }
}
