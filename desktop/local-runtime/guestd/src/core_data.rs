//! The durable half of the core stack -- the volumes, the databases in
//! them, and refusing an image that cannot read what is already there.

use super::*;

/// Is this PostgreSQL saying the data directory itself is the problem?
///
/// The `refuse_incompatible_postgres_data` probe runs first and catches the
/// version case cleanly, in about a second, by reading `PG_VERSION` off the
/// volume. It cannot catch everything: the probe returns "don't know" whenever
/// the volume cannot be read, and "don't know" means proceed. When it does
/// proceed and the server then refuses, this is what turns a 120-second wait
/// ending in a nerdctl error into the offer of a reset.
///
/// Matched on the server's and the image's own words. Deliberately narrow:
/// anything not recognised keeps the old behaviour, which is retryable, and a
/// wrong match here would offer to delete a database over a transient fault.
/// The environment and arguments the PostgreSQL container is created with.
///
/// A free function so the one invariant that matters can be asserted without an
/// engine, a volume or a container: **the cluster path and the volume mount
/// target are the same path**. They were not, and nothing noticed.
///
/// The official image moved `PGDATA` at 18 -- `/var/lib/postgresql/data`
/// through 17, `/var/lib/postgresql/18/docker` after -- while the mount stayed
/// where it was. So on the pinned image the database was written into the
/// image's own anonymous volume and `lemma-postgres-data`, the volume the user
/// is told holds their data, held nothing. A container recreation would have
/// taken every table with it.
///
/// Pinning `PGDATA` rather than inheriting it also keeps the layout stable
/// across the next such move, and puts `PG_VERSION` back at the root of this
/// volume -- which is where `postgres_data_major` looks when it decides whether
/// the data on disk can be opened at all.
pub(crate) fn postgres_container_spec(password: &str) -> (BTreeMap<String, String>, Vec<String>) {
    let environment = BTreeMap::from([
        ("POSTGRES_USER".to_owned(), "postgres".to_owned()),
        ("POSTGRES_PASSWORD".to_owned(), password.to_owned()),
        ("POSTGRES_DB".to_owned(), "lemma".to_owned()),
        ("PGDATA".to_owned(), POSTGRES_DATA_DIR.to_owned()),
    ]);
    let arguments = vec![
        "--network".to_owned(),
        "host".to_owned(),
        "--memory".to_owned(),
        "512m".to_owned(),
        "--cpus".to_owned(),
        "1.5".to_owned(),
        "--volume".to_owned(),
        format!("lemma-postgres-data:{POSTGRES_DATA_DIR}"),
    ];
    (environment, arguments)
}

pub(crate) fn postgres_refused_its_data(diagnostic: &str) -> bool {
    let text = diagnostic.to_ascii_lowercase();
    [
        // The server, on a cluster from another major version.
        "database files are incompatible with server",
        "was initialized by postgresql version",
        // The official image, when it finds data where its older releases kept
        // it. This is the block whose last line is "discussion around this
        // process, and suggestions for how to do so."
        "database directory appears to contain a database",
        "an upgrade is required",
        "incompatible data directory",
        // A cluster that is present but unreadable.
        "could not read file \"global/pg_control\"",
        "is not a valid data directory",
    ]
    .iter()
    .any(|needle| text.contains(needle))
}

impl<E: Engine + 'static> GuestService<E> {
    /// Destroy everything the user made, and nothing else.
    ///
    /// The only global destructive verb in this table -- everything else is
    /// per-sandbox. That is deliberate and worth knowing: its blast radius
    /// equals `system.shutdown`'s, it sits behind the same 0600 app-owned
    /// capability file, and it additionally requires a literal `confirm` so a
    /// replayed or malformed frame cannot trigger it.
    ///
    /// This is the surgical half of a local-data reset. The alternative --
    /// discarding the whole 24 GiB disk from the host -- also works and needs no
    /// cooperation from the guest, but it takes the pulled container images with
    /// it. For the case this was built for, a Postgres major that moved, exactly
    /// one volume needs replacing and re-pulling several hundred megabytes would
    /// be a poor trade.
    ///
    /// Order is the correctness property:
    ///
    /// 1. core containers, so nothing holds the volumes;
    /// 2. sandbox containers, so nothing holds a workspace directory;
    /// 3. the volumes;
    /// 4. the workspace directories.
    ///
    /// Removing workspaces before their containers would leave a running
    /// sandbox bind-mounted onto a path that no longer exists.
    pub(crate) fn reset_data(&self, parameters: Value) -> Result<Value, GuestError> {
        if required_string(&parameters, "confirm")? != "reset-local-data" {
            return Err(GuestError::invalid(
                "a local data reset must be confirmed explicitly",
            ));
        }

        let mut removed_containers = 0;
        for name in ["supertokens", "redis", "postgres"] {
            let container = format!("lemma-core-{name}");
            if self.inspect_raw(&container)?.is_some() {
                self.run_checked(&["rm".into(), "--force".into(), container])?;
                removed_containers += 1;
            }
        }
        removed_containers += self.remove_managed_sandbox_containers()?;

        let mut removed_volumes = 0;
        for volume in ["lemma-postgres-data", "lemma-redis-data"] {
            let output = self
                .engine
                .run(&[
                    "volume".into(),
                    "rm".into(),
                    "--force".into(),
                    volume.into(),
                ])
                .map_err(GuestError::engine)?;
            if output.status.success() {
                removed_volumes += 1;
            }
        }

        let removed_workspaces = self.remove_all_workspaces()?;
        Ok(json!({
            "removed_containers": removed_containers,
            "removed_volumes": removed_volumes,
            "removed_workspaces": removed_workspaces,
        }))
    }

    /// Stop before starting Postgres on a data directory it cannot open.
    ///
    /// `lemma-postgres-data` carries no version in its name, so a release that
    /// moves the Postgres major starts the new server against the old cluster.
    /// Postgres refuses -- correctly -- and the failure arrives as a 120-second
    /// `pg_isready` timeout with a container log nobody reads, on an
    /// installation that will never start again and offers nothing to press.
    /// pg16 -> pg18 is exactly this, and it shipped.
    ///
    /// Compares the cluster's own `PG_VERSION` against the image's own
    /// `PG_MAJOR` rather than a number restated in a manifest: asking the
    /// artifact means bumping the image is the only thing anyone has to
    /// remember. Either side unreadable means proceed -- a fresh volume has no
    /// `PG_VERSION`, which is the common case and must cost nothing.
    pub(crate) fn refuse_incompatible_postgres_data(&self, image: &str) -> Result<(), GuestError> {
        let (Some(found), Some(expected)) = (
            self.postgres_data_major("lemma-postgres-data"),
            self.postgres_image_major(image),
        ) else {
            return Ok(());
        };
        if found == expected {
            return Ok(());
        }
        Err(GuestError {
            code: "postgres_data_incompatible".into(),
            message: format!(
                "the workspace database on this computer was created by PostgreSQL {found} and \
                 this release runs PostgreSQL {expected}; {DATA_RESET_MARKER}"
            ),
            retryable: false,
            status_code: 409,
        })
    }

    /// The major version of the cluster already on a volume, if there is one.
    pub(crate) fn postgres_data_major(&self, volume: &str) -> Option<u32> {
        let inspect = self
            .engine
            .run(&["volume".into(), "inspect".into(), volume.into()])
            .ok()?;
        if !inspect.status.success() {
            return None;
        }
        let parsed: Value = serde_json::from_slice(&inspect.stdout).ok()?;
        let mountpoint = parsed
            .as_array()
            .and_then(|entries| entries.first())
            .unwrap_or(&parsed)
            .get("Mountpoint")?
            .as_str()?;
        let raw = std::fs::read_to_string(Path::new(mountpoint).join("PG_VERSION")).ok()?;
        raw.trim().split('.').next()?.parse().ok()
    }

    /// The major version an image ships, asked of the image itself.
    pub(crate) fn postgres_image_major(&self, image: &str) -> Option<u32> {
        let output = self
            .engine
            .run(&[
                "run".into(),
                "--rm".into(),
                "--network".into(),
                "none".into(),
                "--platform".into(),
                guest_platform().into(),
                image.into(),
                "/usr/bin/printenv".into(),
                "PG_MAJOR".into(),
            ])
            .ok()?;
        if !output.status.success() {
            return None;
        }
        String::from_utf8_lossy(&output.stdout)
            .trim()
            .split('.')
            .next()?
            .parse()
            .ok()
    }

    pub(crate) fn ensure_databases(&self) -> Result<(), GuestError> {
        for database in ["lemma", "lemma_datastore", "supertokens"] {
            self.ensure_database(database, 120)?;
        }
        Ok(())
    }

    pub(crate) fn ensure_database(&self, database: &str, timeout: u64) -> Result<(), GuestError> {
        let deadline = Instant::now() + Duration::from_secs(timeout);
        let query = format!("SELECT 1 FROM pg_database WHERE datname = '{database}'");
        let query_arguments = [
            "exec".into(),
            "lemma-core-postgres".into(),
            "psql".into(),
            "-U".into(),
            "postgres".into(),
            "-tAc".into(),
            query,
        ];
        let create_arguments = [
            "exec".into(),
            "lemma-core-postgres".into(),
            "createdb".into(),
            "-U".into(),
            "postgres".into(),
            database.into(),
        ];
        let mut last_error = None;

        // The official image starts a temporary server for initialization and
        // then restarts it. A CREATE DATABASE transaction can commit just as
        // that connection is closed, leaving `createdb` with exit 1 even
        // though the database now exists. Always use the existence query as
        // the source of truth instead of retrying a potentially committed
        // `createdb` command until it only reports "already exists".
        while Instant::now() < deadline {
            match self.engine.run(&query_arguments) {
                Ok(output) if output.status.success() => {
                    if String::from_utf8_lossy(&output.stdout).trim() == "1" {
                        return Ok(());
                    }
                    match self.engine.run(&create_arguments) {
                        Ok(output) if output.status.success() => {}
                        Ok(output) => {
                            last_error = Some(redact_engine_error(&String::from_utf8_lossy(
                                &output.stderr,
                            )))
                        }
                        Err(error) => last_error = Some(error),
                    }
                }
                Ok(output) => {
                    last_error = Some(redact_engine_error(&String::from_utf8_lossy(
                        &output.stderr,
                    )))
                }
                Err(error) => last_error = Some(error),
            }
            thread::sleep(Duration::from_millis(250));
        }

        Err(GuestError::engine(format!(
            "database {database} provisioning timed out: {}",
            last_error.unwrap_or_else(|| "Postgres did not accept the provisioning query".into())
        )))
    }
}
