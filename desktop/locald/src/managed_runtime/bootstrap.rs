//! What the guest is handed at boot: its manifest, the infrastructure
//! secrets, and the environment the backend reads them from.

pub(crate) use lemma_private_file::{
    ensure_private as ensure_private_file, write_atomic as write_private_atomic,
};

use super::*;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct InfraSecrets {
    pub(crate) postgres_password: String,
    pub(crate) redis_password: String,
}

#[derive(Clone, Debug)]
pub struct ManagedRuntimeBootstrap {
    artifact_root: PathBuf,
    bridge_executable: PathBuf,
    #[cfg(target_os = "macos")]
    vz_executable: PathBuf,
    #[cfg(windows)]
    wsl_executable: PathBuf,
    secrets: InfraSecrets,
}

impl ManagedRuntimeBootstrap {
    pub fn discover(paths: &LocalPaths, healed: &mut Vec<String>) -> io::Result<Option<Self>> {
        let Some(artifact_root) = env::var_os("LEMMA_LOCALD_MANAGED_RUNTIME_ARTIFACT_ROOT")
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
        else {
            return Ok(None);
        };
        if !artifact_root.is_dir() {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                format!(
                    "managed runtime artifact root is missing: {}",
                    artifact_root.display()
                ),
            ));
        }

        #[cfg(not(any(target_os = "macos", windows)))]
        return Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "managed local runtime is supported on macOS and Windows",
        ));

        #[cfg(any(target_os = "macos", windows))]
        {
            let bridge_executable = bundled_executable(
                "LEMMA_LOCALD_RUNTIME_BRIDGE_BIN",
                if cfg!(windows) {
                    "lemma-runtime.exe"
                } else {
                    "lemma-runtime"
                },
            )?;
            #[cfg(target_os = "macos")]
            let vz_executable = bundled_executable("LEMMA_LOCALD_VZ_BIN", "lemma-vz")?;
            #[cfg(windows)]
            let wsl_executable = env::var_os("LEMMA_LOCALD_WSL_BIN")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("wsl.exe"));

            Ok(Some(Self {
                artifact_root,
                bridge_executable,
                #[cfg(target_os = "macos")]
                vz_executable,
                #[cfg(windows)]
                wsl_executable,
                secrets: load_or_create_secrets(&paths.root.join("infra.secrets.json"), healed)?,
            }))
        }
    }

    pub(crate) fn manifest_material(&self) -> ManagedManifestMaterial {
        ManagedManifestMaterial {
            postgres_password: self.secrets.postgres_password.clone(),
            redis_password: self.secrets.redis_password.clone(),
            bridge_executable: self.bridge_executable.clone(),
        }
    }

    pub fn controller(
        &self,
        paths: &LocalPaths,
        spec: ManagedRuntimeSpec,
    ) -> io::Result<Arc<ManagedRuntimeController>> {
        if spec.credentials.postgres_password != self.secrets.postgres_password
            || spec.credentials.redis_password != self.secrets.redis_password
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "host manifest credentials do not match the private local installation",
            ));
        }
        let runtime = ManagedRuntime::new(ManagedRuntimeConfig {
            wsl_distribution: wsl_distribution_for(&paths.root),
            local_root: paths.root.clone(),
            artifact_root: self.artifact_root.clone(),
            bridge_executable: self.bridge_executable.clone(),
            #[cfg(target_os = "macos")]
            vz_executable: self.vz_executable.clone(),
            #[cfg(windows)]
            wsl_executable: self.wsl_executable.clone(),
        })?;
        Ok(Arc::new(ManagedRuntimeController {
            runtime,
            spec,
            forwarders: Mutex::new(Vec::new()),
            probes: Mutex::new(ProbeTracker::default()),
            status: Mutex::new(None),
            clock_keeper: Mutex::new(None),
            last_clock_error: Mutex::new(None),
            sandbox_images: Mutex::new(SandboxImageStatus::default()),
            pending_auth: Mutex::new(None),
            pending_images: Mutex::new(None),
            cancellation: lemma_desktop_process::Cancellation::default(),
        }))
    }
}

/// Read the infrastructure passwords, replacing them only if unreadable.
///
/// An unreadable file used to end the daemon permanently. Healing it needs one
/// extra step that ordinary self-healing does not: `postgres_password` was
/// baked into the `lemma-postgres-data` volume at `initdb`, so a new password
/// does not open the existing database. `ensure_core_container` only replaces a
/// container when its image or config generation changes, and `ensure_database`
/// connects over the local socket with no password at all -- so the mismatch
/// survives the entire guest start and first surfaces deep in the backend's
/// migrations as an opaque auth error.
///
/// So the replacement is recorded as "this installation's data can no longer be
/// read", which `start_host_packs` refuses on, with a reset the user can press.
pub(crate) fn load_or_create_secrets(
    path: &Path,
    healed: &mut Vec<String>,
) -> io::Result<InfraSecrets> {
    if path.is_file() {
        match read_existing_secrets(path) {
            Ok(secrets) => return Ok(secrets),
            Err(reason) => {
                let aside = crate::paths::quarantine_aside(path)?;
                if let Some(root) = path.parent() {
                    crate::paths::require_data_reset(
                        root,
                        "the private infrastructure passwords were replaced, and the existing \
                         workspace database was created with the previous ones",
                    )?;
                }
                healed.push(format!(
                    "the infrastructure passwords were unreadable ({reason}); kept as {} and \
                     replaced. The existing local data cannot be opened with the new ones",
                    aside.display()
                ));
            }
        }
    }
    // Missing, rather than unreadable. Same consequence: the password baked
    // into the Postgres volume at `initdb` does not change because this file
    // was recreated, so the new one opens nothing. Only a genuine first run may
    // mint quietly, and a first run has no data.
    else if path
        .parent()
        .is_some_and(crate::paths::installation_has_data)
    {
        let root = path.parent().expect("checked just above");
        crate::paths::require_data_reset(
            root,
            "the private infrastructure passwords are missing, and the existing workspace \
             database was created with the previous ones",
        )?;
        healed.push(
            "the infrastructure passwords were missing while local data was still present; \
             new ones were created and the existing database cannot be opened with them"
                .to_owned(),
        );
    }
    let secrets = InfraSecrets {
        postgres_password: random_hex()?,
        redis_password: random_hex()?,
    };
    write_private_atomic(path, &serde_json::to_vec(&secrets)?)?;
    Ok(secrets)
}

pub(crate) fn read_existing_secrets(path: &Path) -> Result<InfraSecrets, String> {
    ensure_private_file(path).map_err(|error| error.to_string())?;
    let raw = fs::read(path).map_err(|error| error.to_string())?;
    let secrets: InfraSecrets =
        serde_json::from_slice(&raw).map_err(|error| format!("invalid JSON: {error}"))?;
    validate_secret("postgres_password", &secrets.postgres_password)
        .map_err(|error| error.to_string())?;
    validate_secret("redis_password", &secrets.redis_password)
        .map_err(|error| error.to_string())?;
    Ok(secrets)
}

pub(crate) fn random_hex() -> io::Result<String> {
    let mut bytes = [0_u8; 32];
    getrandom::fill(&mut bytes)
        .map_err(|error| io::Error::other(format!("secure randomness failed: {error}")))?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

pub(crate) fn validate_secret(name: &str, value: &str) -> io::Result<()> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("managed {name} must be a 64-character lowercase hex secret"),
        ));
    }
    Ok(())
}

impl ManagedRuntimeController {
    pub fn backend_environment(&self) -> io::Result<HashMap<String, String>> {
        let status = self.status().ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::NotConnected,
                "private runtime is not ready for host processes",
            )
        })?;
        let (host, postgres, redis, supertokens) = if cfg!(target_os = "macos") {
            (
                Ipv4Addr::LOCALHOST,
                self.spec.ports.postgres,
                self.spec.ports.redis,
                self.spec.ports.supertokens,
            )
        } else {
            (
                // This route reaches the guest by address, so unlike macOS it
                // genuinely needs one. A guest that reports none is healthy
                // but unreachable this way, and saying so beats a parse error.
                private_ipv4(
                    status.endpoint_host.as_deref().ok_or_else(|| {
                        io::Error::new(
                            io::ErrorKind::AddrNotAvailable,
                            "the private runtime reported no network address, so host \
                             services cannot reach its database",
                        )
                    })?,
                    "guest endpoint",
                )?,
                5432,
                6379,
                3567,
            )
        };
        let capability_file = runtime_path_value(self.runtime.capability_file())?;
        let control_socket = runtime_path_value(self.runtime.control_socket())?;
        Ok(HashMap::from([
            (
                "DATABASE_URL".into(),
                format!(
                    "postgresql+asyncpg://postgres:{}@{host}:{postgres}/lemma",
                    self.spec.credentials.postgres_password
                ),
            ),
            (
                "DATASTORE_DATABASE_URL".into(),
                format!(
                    "postgresql+asyncpg://postgres:{}@{host}:{postgres}/lemma_datastore",
                    self.spec.credentials.postgres_password
                ),
            ),
            (
                "REDIS_URL".into(),
                format!(
                    "redis://:{}@{host}:{redis}",
                    self.spec.credentials.redis_password
                ),
            ),
            (
                "SUPERTOKENS_CORE_URL".into(),
                format!("http://{host}:{supertokens}"),
            ),
            // The backend invokes the narrow runtime bridge for the sandbox runtime
            // lifecycle operations. Pass explicit paths to the app-owned
            // capability and transport; the bridge must never guess from a
            // developer checkout or rewrite a localhost URL.
            ("LEMMA_GUEST_CAPABILITY_FILE".into(), capability_file),
            ("LEMMA_GUEST_CONTROL_SOCKET".into(), control_socket),
            (
                "LEMMA_WSL_DISTRIBUTION".into(),
                self.runtime.wsl_distribution().into(),
            ),
        ]))
    }
}
