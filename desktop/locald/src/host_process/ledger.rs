//! What this installation started, written down so a replaced daemon can
//! find it again.

use super::*;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ProcessLedger {
    pub(crate) schema_version: u64,
    pub(crate) installation_id: String,
    pub(crate) entries: Vec<ProcessLedgerEntry>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ProcessLedgerEntry {
    pub(crate) service_id: String,
    pub(crate) pid: u32,
    pub(crate) executable: String,
    pub(crate) start_identity: String,
    pub(crate) installation_id: String,
    pub(crate) runtime_generation: String,
}

pub(crate) fn reclaim_persisted_installation_processes(state_root: &Path) -> io::Result<()> {
    let manifest_path = state_root.join("host-pack.json");
    let raw = match fs::read_to_string(&manifest_path) {
        Ok(raw) => raw,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
    };
    // A damaged prior manifest is not sufficient proof of ownership. Leave
    // every process untouched; dynamic port allocation will safely route
    // around any listener that remains.
    let Ok(manifest) = serde_json::from_str::<HostPackManifest>(&raw) else {
        return Ok(());
    };
    let installation_id = load_or_create_installation_id(state_root)?;
    reclaim_verified_processes(
        &state_root.join("processes.json"),
        &installation_id,
        &manifest,
    )
}

pub(crate) fn load_or_create_installation_id(root: &Path) -> io::Result<String> {
    let path = root.join("installation.id");
    if let Ok(value) = fs::read_to_string(&path) {
        let value = value.trim();
        if value.len() == 32 && value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return Ok(value.to_owned());
        }
    }
    let value = random_generation()?;
    write_private_atomic(&path, format!("{value}\n").as_bytes())?;
    Ok(value)
}

pub(crate) fn installation_identity(root: &Path) -> io::Result<String> {
    load_or_create_installation_id(root)
}

pub(crate) fn read_process_ledger(path: &Path) -> Option<ProcessLedger> {
    let raw = fs::read(path).ok()?;
    if raw.len() > 1024 * 1024 {
        return None;
    }
    let ledger = serde_json::from_slice::<ProcessLedger>(&raw).ok()?;
    (ledger.schema_version == PROCESS_LEDGER_SCHEMA_VERSION).then_some(ledger)
}

pub(crate) fn write_process_ledger(path: &Path, ledger: &ProcessLedger) -> io::Result<()> {
    write_private_atomic(path, &serde_json::to_vec_pretty(ledger)?)
}

pub(crate) fn write_private_atomic(path: &Path, contents: &[u8]) -> io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::other("process ledger has no parent"))?;
    fs::create_dir_all(parent)?;
    let temporary = parent.join(format!(
        ".processes-{}-{}.tmp",
        std::process::id(),
        random_generation()?
    ));
    let _ = fs::remove_file(&temporary);
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temporary)?;
    file.write_all(contents)?;
    file.sync_all()?;
    replace_private_file(&temporary, path)?;
    #[cfg(unix)]
    File::open(parent)?.sync_all()?;
    Ok(())
}

pub(crate) fn replace_private_file(source: &Path, destination: &Path) -> io::Result<()> {
    // No delete-then-rename on Windows. `fs::rename` is MoveFileExW with
    // MOVEFILE_REPLACE_EXISTING and already replaces the destination, so the
    // unlink bought nothing and cost two things: a window in which the process
    // ledger simply did not exist -- crash there and the previous backend and
    // frontend are never reclaimed, and keep their ports -- and a second way to
    // fail, since removing a file anything has open (a virus scanner, moments
    // after it was written) is a sharing violation.
    fs::rename(source, destination)
}

pub(crate) fn reclaim_verified_processes(
    ledger_path: &Path,
    installation_id: &str,
    manifest: &HostPackManifest,
) -> io::Result<()> {
    let Some(ledger) = read_process_ledger(ledger_path) else {
        return Ok(());
    };
    if ledger.installation_id != installation_id {
        return Ok(());
    }
    for entry in &ledger.entries {
        if entry.installation_id != installation_id
            || entry.runtime_generation.len() != 32
            || !entry
                .runtime_generation
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit())
        {
            continue;
        }
        let Some(spec) = manifest
            .services
            .iter()
            .find(|spec| spec.id == entry.service_id)
        else {
            continue;
        };
        let Some(expected) = spec
            .command
            .first()
            .and_then(|path| Path::new(path).canonicalize().ok())
        else {
            continue;
        };
        let Ok(identity) = process_identity(entry.pid) else {
            continue;
        };
        if identity.executable == entry.executable
            && identity.start_identity == entry.start_identity
            && Path::new(&identity.executable)
                .canonicalize()
                .is_ok_and(|actual| actual == expected)
        {
            terminate_verified_process(entry.pid)?;
        }
    }
    write_process_ledger(
        ledger_path,
        &ProcessLedger {
            schema_version: PROCESS_LEDGER_SCHEMA_VERSION,
            installation_id: installation_id.to_owned(),
            entries: Vec::new(),
        },
    )
}

impl HostProcessManager {
    pub(crate) fn record_child(&self, id: &str, child: &mut Child) -> io::Result<()> {
        let _guard = self
            .process_ledger_lock
            .lock()
            .expect("process ledger lock poisoned");
        let identity = settled_process_identity(child)?;
        let generation = self
            .runtime_generation
            .lock()
            .expect("runtime generation lock poisoned")
            .clone();
        let mut ledger = read_process_ledger(&self.process_ledger_path)
            .filter(|ledger| ledger.installation_id == self.installation_id)
            .unwrap_or_else(|| ProcessLedger {
                schema_version: PROCESS_LEDGER_SCHEMA_VERSION,
                installation_id: self.installation_id.clone(),
                entries: Vec::new(),
            });
        ledger.entries.retain(|entry| entry.service_id != id);
        ledger.entries.push(ProcessLedgerEntry {
            service_id: id.to_owned(),
            pid: child.id(),
            executable: identity.executable,
            start_identity: identity.start_identity,
            installation_id: self.installation_id.clone(),
            runtime_generation: generation,
        });
        write_process_ledger(&self.process_ledger_path, &ledger)
    }

    pub(crate) fn remove_ledger_entry(&self, id: &str) -> io::Result<()> {
        let _guard = self
            .process_ledger_lock
            .lock()
            .expect("process ledger lock poisoned");
        let Some(mut ledger) = read_process_ledger(&self.process_ledger_path) else {
            return Ok(());
        };
        if ledger.installation_id != self.installation_id {
            return Ok(());
        }
        ledger.entries.retain(|entry| entry.service_id != id);
        write_process_ledger(&self.process_ledger_path, &ledger)
    }
}
