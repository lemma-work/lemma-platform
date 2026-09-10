//! Turning a sandbox spec into engine arguments, and the files on disk
//! that back one.

use super::*;

/// How much log one sandbox may keep, per file and in total.
///
/// Split rather than one big file so rotation actually frees space: a single
/// capped file is truncated, which loses everything, while three rotated ones
/// keep the recent past and drop the distant one.
const SANDBOX_LOG_FILE_MIB: u32 = 16;
const SANDBOX_LOG_FILES: u32 = 3;

pub(crate) fn build_run_arguments(
    parameters: &EnsureParameters,
    workspace: Option<&Path>,
    runtime_token: Option<&Path>,
    env_file: &Path,
    host_gateway: &str,
) -> Vec<String> {
    let metadata = serde_json::to_string(&parameters.metadata)
        .expect("validated sandbox metadata must serialize");
    let mut arguments = vec![
        "run".into(),
        "--detach".into(),
        "--platform".into(),
        guest_platform().into(),
        "--name".into(),
        container_name(&parameters.sandbox_id),
        "--label".into(),
        MANAGED_LABEL.into(),
        "--label".into(),
        format!("lemma.work/sandbox-id={}", parameters.sandbox_id),
        "--label".into(),
        "lemma.work/provider=lemma_local".into(),
        "--label".into(),
        format!(
            "lemma.work/workload-kind={}",
            match parameters.workload_kind {
                WorkloadKind::Workspace => "workspace",
                WorkloadKind::Function => "function",
            }
        ),
        "--label".into(),
        format!("lemma.work/image-ref={}", parameters.image),
        "--label".into(),
        format!("lemma.work/metadata={metadata}"),
        "--env-file".into(),
        env_file.display().to_string(),
        "--add-host".into(),
        format!("host.lemma.internal:{host_gateway}"),
        // Bounded, because these write to the guest's data disk and that disk
        // is a fixed size. A sandbox with a chatty loop in it -- an agent
        // retrying, a dependency printing a warning per file -- had nothing
        // stopping its log from growing until the disk was full, and a full
        // data disk is not a lost sandbox: it is Postgres and everything else
        // in the guest stopping too.
        //
        // Enough to debug a failure with, not enough to be a problem: three
        // files at 16 MiB is 48 MiB per container, and the newest is always
        // the one being written.
        "--log-opt".into(),
        format!("max-size={SANDBOX_LOG_FILE_MIB}m"),
        "--log-opt".into(),
        format!("max-file={SANDBOX_LOG_FILES}"),
    ];
    match parameters.workload_kind {
        WorkloadKind::Workspace => {
            let workspace = workspace.expect("workspace workload must have storage");
            let runtime_token =
                runtime_token.expect("workspace workload must have a runtime token");
            let runtime_token_mount = runtime_token
                .parent()
                .expect("workspace runtime token must have a private directory");
            arguments.extend([
                "--mount".into(),
                format!("type=bind,src={},dst=/workspace", workspace.display()),
                "--mount".into(),
                format!(
                    "type=bind,src={},dst=/run/lemma-bootstrap",
                    runtime_token_mount.display()
                ),
                "--workdir".into(),
                "/workspace".into(),
            ]);
        }
        WorkloadKind::Function => {
            arguments.extend([
                "--read-only".into(),
                "--tmpfs".into(),
                "/tmp:rw,noexec,nosuid,size=512m,uid=10001,gid=10001".into(),
                "--tmpfs".into(),
                "/run/lemma-function-cache:rw,exec,nosuid,nodev,size=512m,mode=0700,uid=10001,gid=10001"
                    .into(),
                "--env".into(),
                "LEMMA_FUNCTION_CACHE_ROOT=/run/lemma-function-cache".into(),
                "--workdir".into(),
                "/tmp".into(),
            ]);
        }
    }
    for app in &parameters.apps {
        arguments.extend(["--publish".into(), format!("0.0.0.0::{}", app.port)]);
    }
    if let Some(memory) = parameters
        .resources
        .memory
        .as_deref()
        .filter(|v| !v.is_empty())
    {
        arguments.extend(["--memory".into(), memory.into()]);
    }
    if let Some(cpus) = parameters
        .resources
        .cpus
        .as_deref()
        .filter(|v| !v.is_empty())
    {
        arguments.extend(["--cpus".into(), cpus.into()]);
    }
    arguments.push(parameters.image.clone());
    arguments
}

#[cfg(target_arch = "aarch64")]
pub(crate) fn guest_platform() -> &'static str {
    "linux/arm64"
}

#[cfg(target_arch = "x86_64")]
pub(crate) fn guest_platform() -> &'static str {
    "linux/amd64"
}

pub(crate) fn container_name(sandbox_id: &str) -> String {
    format!("{CONTAINER_PREFIX}{sandbox_id}")
}

impl<E: Engine + 'static> GuestService<E> {
    pub(crate) fn workspace(&self, sandbox_id: &str) -> Result<PathBuf, GuestError> {
        let root = self.state_root.join("workspaces");
        let path = root.join(sandbox_id);
        if path.parent() != Some(root.as_path()) {
            return Err(GuestError::invalid("workspace escaped managed root"));
        }
        fs::create_dir_all(&path).map_err(|error| GuestError::engine(error.to_string()))?;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700))
            .map_err(|error| GuestError::engine(error.to_string()))?;
        // SAFETY: the path is a freshly validated child of the private managed
        // root and the runtime image's fixed workspace UID/GID is 10001.
        let path_bytes = std::ffi::CString::new(path.as_os_str().as_encoded_bytes())
            .map_err(|_| GuestError::invalid("workspace path contains NUL"))?;
        let result = unsafe { libc::chown(path_bytes.as_ptr(), 10_001, 10_001) };
        if result != 0 {
            return Err(GuestError::engine(io::Error::last_os_error().to_string()));
        }
        Ok(path)
    }

    pub(crate) fn purge_workspace(&self, sandbox_id: &str) -> Result<bool, GuestError> {
        let root = self.state_root.join("workspaces");
        let path = root.join(sandbox_id);
        if path.parent() != Some(root.as_path()) {
            return Err(GuestError::invalid("workspace escaped managed root"));
        }
        if !path.exists() {
            return Ok(false);
        }
        fs::remove_dir_all(path).map_err(|error| GuestError::engine(error.to_string()))?;
        Ok(true)
    }

    pub(crate) fn write_env_file(
        &self,
        sandbox_id: &str,
        environment: &BTreeMap<String, String>,
    ) -> Result<PathBuf, GuestError> {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let path = self
            .state_root
            .join("run")
            .join(format!("env-{sandbox_id}-{}-{nonce}", std::process::id()));
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&path)
            .map_err(|error| GuestError::engine(error.to_string()))?;
        for (name, value) in environment {
            writeln!(file, "{name}={value}")
                .map_err(|error| GuestError::engine(error.to_string()))?;
        }
        Ok(path)
    }

    pub(crate) fn runtime_token_dir(&self, sandbox_id: &str) -> Result<PathBuf, GuestError> {
        let root = self.state_root.join("run");
        let path = root.join(format!("runtime-token-{sandbox_id}"));
        if path.parent() != Some(root.as_path()) {
            return Err(GuestError::invalid("runtime token escaped managed root"));
        }
        Ok(path)
    }

    pub(crate) fn runtime_token_path(&self, sandbox_id: &str) -> Result<PathBuf, GuestError> {
        Ok(self.runtime_token_dir(sandbox_id)?.join("token"))
    }

    pub(crate) fn write_runtime_token(
        &self,
        sandbox_id: &str,
        token: &str,
    ) -> Result<PathBuf, GuestError> {
        if token.is_empty() || token.len() > 4096 || token.contains('\0') {
            return Err(GuestError::invalid("workspace runtime token is invalid"));
        }
        let directory = self.runtime_token_dir(sandbox_id)?;
        match fs::symlink_metadata(&directory) {
            Ok(metadata) if metadata.is_dir() => {}
            Ok(_) => fs::remove_file(&directory)
                .map_err(|error| GuestError::engine(error.to_string()))?,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(GuestError::engine(error.to_string())),
        }
        fs::create_dir_all(&directory).map_err(|error| GuestError::engine(error.to_string()))?;
        fs::set_permissions(&directory, fs::Permissions::from_mode(0o700))
            .map_err(|error| GuestError::engine(error.to_string()))?;
        let directory_bytes = std::ffi::CString::new(directory.as_os_str().as_encoded_bytes())
            .map_err(|_| GuestError::invalid("runtime token directory contains NUL"))?;
        let result = unsafe { libc::chown(directory_bytes.as_ptr(), 10_001, 10_001) };
        if result != 0 {
            return Err(GuestError::engine(io::Error::last_os_error().to_string()));
        }

        let path = self.runtime_token_path(sandbox_id)?;
        match fs::remove_file(&path) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(GuestError::engine(error.to_string())),
        }
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&path)
            .map_err(|error| GuestError::engine(error.to_string()))?;
        file.write_all(token.as_bytes())
            .map_err(|error| GuestError::engine(error.to_string()))?;
        file.sync_all()
            .map_err(|error| GuestError::engine(error.to_string()))?;
        let path_bytes = std::ffi::CString::new(path.as_os_str().as_encoded_bytes())
            .map_err(|_| GuestError::invalid("runtime token path contains NUL"))?;
        let result = unsafe { libc::chown(path_bytes.as_ptr(), 10_001, 10_001) };
        if result != 0 {
            return Err(GuestError::engine(io::Error::last_os_error().to_string()));
        }
        Ok(path)
    }

    pub(crate) fn remove_runtime_token(&self, sandbox_id: &str) -> Result<(), GuestError> {
        let directory = self.runtime_token_dir(sandbox_id)?;
        match fs::symlink_metadata(&directory) {
            Ok(metadata) if metadata.is_dir() => {
                fs::remove_dir_all(directory).map_err(|error| GuestError::engine(error.to_string()))
            }
            Ok(_) => {
                fs::remove_file(directory).map_err(|error| GuestError::engine(error.to_string()))
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(GuestError::engine(error.to_string())),
        }
    }
}
