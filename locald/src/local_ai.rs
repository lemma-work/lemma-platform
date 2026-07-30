use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::net::{Ipv4Addr, TcpListener};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::host_process::{process_identity, terminate_verified_process};

const CONFIG_SCHEMA_VERSION: u64 = 2;
const MARKER_SCHEMA_VERSION: u64 = 2;
const PROCESS_MARKER_SCHEMA_VERSION: u64 = 2;
const DISK_HEADROOM_BYTES: u64 = 4 * 1024 * 1024 * 1024;
const DOWNLOAD_POLL_INTERVAL: Duration = Duration::from_millis(500);
// Keep local generations bounded. Thinking models can otherwise consume the
// entire server default in hidden reasoning, leaving later requests queued for
// hours even though the model process itself remains healthy.
const MAX_OUTPUT_TOKENS: u32 = 4_096;
const MAX_OUTPUT_TOKENS_ARG: &str = "4096";
// This caps reusable prompt caches, not total unified memory. MLX itself uses
// Metal's max_recommended_working_set_size so macOS can manage memory pressure.
const PROMPT_CACHE_BYTES: &str = "1GB";
// One active decode keeps a large agent context from being duplicated in
// unified memory on 16 GB machines. Tool-using agent turns remain sequential.
const DECODE_CONCURRENCY: &str = "1";

const BONSAI_ID: &str = "prism-ml/Ternary-Bonsai-8B-mlx-2bit";
const BONSAI_REVISION: &str = "9260b24298e4211e804663e9f519962cf59f34be";
const BONSAI_DOWNLOAD_BYTES: u64 = 2_315_166_534;
const LEGACY_BONSAI_DOWNLOAD_BYTES: u64 = 2_315_264_643;
const BONSAI_WEIGHTS_BYTES: u64 = 2_303_661_704;
const BONSAI_WEIGHTS_SHA256: &str =
    "f43270cbae86830b7eecb25bb8a0a0a005a81f180b68868dc39c755cebfff362";
const BONSAI_TOKENIZER_BYTES: u64 = 11_422_650;
const BONSAI_TOKENIZER_SHA256: &str =
    "be75606093db2094d7cd20f3c2f385c212750648bd6ea4fb2bf507a6a4c55506";
const BONSAI_FILES: &[ModelFile] = &[
    ModelFile::new("LICENSE", 10_174),
    ModelFile::new("NOTICE.txt", 412),
    ModelFile::new("chat_template.jinja", 4_063),
    ModelFile::new("config.json", 3_118),
    ModelFile::new("model.safetensors", BONSAI_WEIGHTS_BYTES),
    ModelFile::new("model.safetensors.index.json", 64_065),
    ModelFile::new("tokenizer.json", BONSAI_TOKENIZER_BYTES),
    ModelFile::new("tokenizer_config.json", 348),
];

const QWEN_ID: &str = "mlx-community/Qwen3-4B-4bit";
const QWEN_REVISION: &str = "4dcb3d101c2a062e5c1d4bb173588c54ea6c4d25";
const QWEN_DOWNLOAD_BYTES: u64 = 2_278_969_756;
const QWEN_WEIGHTS_BYTES: u64 = 2_263_022_529;
const QWEN_WEIGHTS_SHA256: &str =
    "e240c0bdc0ebb0681bf0da0f98d9719fd6ebe269a3633f81542c13e81345651d";
const QWEN_TOKENIZER_BYTES: u64 = 11_422_654;
const QWEN_TOKENIZER_SHA256: &str =
    "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4";
const QWEN_FILES: &[ModelFile] = &[
    ModelFile::new("added_tokens.json", 707),
    ModelFile::new("config.json", 937),
    ModelFile::new("merges.txt", 1_671_853),
    ModelFile::new("model.safetensors", QWEN_WEIGHTS_BYTES),
    ModelFile::new("model.safetensors.index.json", 63_924),
    ModelFile::new("special_tokens_map.json", 613),
    ModelFile::new("tokenizer.json", QWEN_TOKENIZER_BYTES),
    ModelFile::new("tokenizer_config.json", 9_706),
    ModelFile::new("vocab.json", 2_776_833),
];

const MODEL_SPECS: &[ModelSpec] = &[
    ModelSpec {
        id: BONSAI_ID,
        slug: "ternary-bonsai-8b-mlx-2bit",
        name: "Ternary Bonsai 8B",
        description: "Best quality per GB · compact ternary 8B",
        license: "Apache 2.0",
        license_url: "https://huggingface.co/prism-ml/Ternary-Bonsai-8B-mlx-2bit/blob/9260b24298e4211e804663e9f519962cf59f34be/LICENSE",
        revision: BONSAI_REVISION,
        download_bytes: BONSAI_DOWNLOAD_BYTES,
        files: BONSAI_FILES,
        weights_sha256: BONSAI_WEIGHTS_SHA256,
        tokenizer_sha256: BONSAI_TOKENIZER_SHA256,
        context_tokens: 65_536,
        thinking: true,
        tool_calling: true,
    },
    ModelSpec {
        id: QWEN_ID,
        slug: "qwen3-4b-mlx-4bit",
        name: "Qwen3 4B",
        description: "Fast, tool-capable everyday model · 4-bit",
        license: "Apache 2.0",
        license_url: "https://huggingface.co/Qwen/Qwen3-4B/blob/main/LICENSE",
        revision: QWEN_REVISION,
        download_bytes: QWEN_DOWNLOAD_BYTES,
        files: QWEN_FILES,
        weights_sha256: QWEN_WEIGHTS_SHA256,
        tokenizer_sha256: QWEN_TOKENIZER_SHA256,
        context_tokens: 40_960,
        thinking: true,
        tool_calling: true,
    },
];

#[derive(Clone, Copy, Debug)]
struct ModelFile {
    name: &'static str,
    bytes: u64,
}

impl ModelFile {
    const fn new(name: &'static str, bytes: u64) -> Self {
        Self { name, bytes }
    }
}

#[derive(Clone, Copy, Debug)]
struct ModelSpec {
    id: &'static str,
    slug: &'static str,
    name: &'static str,
    description: &'static str,
    license: &'static str,
    license_url: &'static str,
    revision: &'static str,
    download_bytes: u64,
    files: &'static [ModelFile],
    weights_sha256: &'static str,
    tokenizer_sha256: &'static str,
    context_tokens: u32,
    thinking: bool,
    tool_calling: bool,
}

#[derive(Clone, Debug)]
struct MlxRuntime {
    python: PathBuf,
    python_path: PathBuf,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct LocalAiConfig {
    schema_version: u64,
    enabled: bool,
    port: Option<u16>,
    #[serde(default = "default_model_id")]
    selected_model_id: String,
}

impl Default for LocalAiConfig {
    fn default() -> Self {
        Self {
            schema_version: CONFIG_SCHEMA_VERSION,
            enabled: false,
            port: None,
            selected_model_id: default_model_id(),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct InstallMarker {
    schema_version: u64,
    model_id: String,
    revision: String,
    download_bytes: u64,
    weights_sha256: String,
    tokenizer_sha256: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ProcessMarker {
    schema_version: u64,
    model_id: String,
    revision: String,
    pid: u32,
    executable: String,
    start_identity: String,
}

#[derive(Clone, Debug)]
struct OperationState {
    action: String,
    stage: String,
    model_id: String,
    progress: u8,
    downloaded_bytes: Option<u64>,
    total_bytes: Option<u64>,
    throughput_bytes_per_second: Option<u64>,
    eta_seconds: Option<u64>,
}

struct LocalAiState {
    child: Option<Child>,
    running_model_id: Option<String>,
    operation: Option<OperationState>,
    last_error: Option<String>,
}

impl Default for LocalAiState {
    fn default() -> Self {
        Self {
            child: None,
            running_model_id: None,
            operation: None,
            last_error: None,
        }
    }
}

pub struct LocalAiManager {
    root: PathBuf,
    config_path: PathBuf,
    legacy_marker_path: PathBuf,
    process_marker_path: PathBuf,
    log_path: PathBuf,
    runtime: Option<MlxRuntime>,
    config: Mutex<LocalAiConfig>,
    state: Mutex<LocalAiState>,
    lifecycle: Mutex<()>,
    desired_running: AtomicBool,
    release_requested: AtomicBool,
}

impl LocalAiManager {
    pub fn load(root: &Path, host_pack_root: Option<&Path>) -> io::Result<Self> {
        let local_ai_root = root.join("local-ai");
        fs::create_dir_all(local_ai_root.join("models"))?;
        set_private_dir(&local_ai_root)?;
        set_private_dir(&local_ai_root.join("models"))?;
        let config_path = local_ai_root.join("config.json");
        let config = read_config(&config_path)?;
        let process_marker_path = local_ai_root.join("process.json");
        let legacy_marker_path = local_ai_root.join("install.json");
        let log_path = root.join("logs/local-ai.log");
        let runtime = discover_runtime(host_pack_root);
        migrate_legacy_bonsai_marker(&local_ai_root, &legacy_marker_path)?;
        reclaim_owned_process(&process_marker_path, runtime.as_ref())?;
        Ok(Self {
            root: local_ai_root,
            config_path,
            legacy_marker_path,
            process_marker_path,
            log_path,
            runtime,
            desired_running: AtomicBool::new(config.enabled),
            release_requested: AtomicBool::new(false),
            config: Mutex::new(config),
            state: Mutex::new(LocalAiState::default()),
            lifecycle: Mutex::new(()),
        })
    }

    pub fn supported(&self) -> bool {
        cfg!(all(target_os = "macos", target_arch = "aarch64"))
    }

    pub fn runtime_available(&self) -> bool {
        self.supported() && self.runtime.is_some()
    }

    pub fn enabled(&self) -> bool {
        self.config
            .lock()
            .expect("local AI config lock poisoned")
            .enabled
    }

    pub fn selected_model_id(&self) -> String {
        self.config
            .lock()
            .expect("local AI config lock poisoned")
            .selected_model_id
            .clone()
    }

    pub fn is_selected(&self, model_id: &str) -> bool {
        self.selected_model_id() == model_id
    }

    pub fn model_name(&self) -> io::Result<String> {
        let model_id = self.selected_model_id();
        let spec = model_spec(&model_id)?;
        if !self.install_marker_valid(spec) {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                format!("{} is not installed", spec.name),
            ));
        }
        path_text(&self.model_root(spec).canonicalize()?)
    }

    pub fn base_url(&self) -> Option<String> {
        self.config
            .lock()
            .expect("local AI config lock poisoned")
            .port
            .map(|port| format!("http://127.0.0.1:{port}/v1"))
    }

    pub fn status(&self) -> Value {
        self.inspect_exit();
        let config = self
            .config
            .lock()
            .expect("local AI config lock poisoned")
            .clone();
        let state = self.state.lock().expect("local AI state lock poisoned");
        let selected_spec = model_spec(&config.selected_model_id).unwrap_or(&MODEL_SPECS[0]);
        let selected_installed = self.install_marker_valid(selected_spec);
        let running = state.child.is_some();
        let running_model_id = state.running_model_id.clone();
        let status = if !self.supported() {
            "unsupported"
        } else if self.runtime.is_none() {
            "runtime_unavailable"
        } else if let Some(operation) = state.operation.as_ref() {
            operation.stage.as_str()
        } else if running {
            "ready"
        } else if state.last_error.is_some() {
            "error"
        } else if selected_installed {
            "stopped"
        } else {
            "not_installed"
        };
        let operation = state.operation.as_ref();
        let models = MODEL_SPECS
            .iter()
            .map(|spec| {
                let installed = self.install_marker_valid(spec);
                let model_running = running_model_id.as_deref() == Some(spec.id) && running;
                let model_operation = operation.filter(|operation| operation.model_id == spec.id);
                let model_status = if let Some(operation) = model_operation {
                    operation.stage.as_str()
                } else if model_running {
                    "ready"
                } else if installed {
                    "stopped"
                } else {
                    "not_installed"
                };
                json!({
                    "id": spec.id,
                    "slug": spec.slug,
                    "name": spec.name,
                    "description": spec.description,
                    "license": spec.license,
                    "license_url": spec.license_url,
                    "revision": spec.revision,
                    "download_bytes": spec.download_bytes,
                    "context_tokens": spec.context_tokens,
                    "thinking": spec.thinking,
                    "tool_calling": spec.tool_calling,
                    "installed_bytes": if installed { directory_payload_bytes(&self.model_root(spec), spec) } else { 0 },
                    "installed": installed,
                    "selected": config.selected_model_id == spec.id,
                    "running": model_running,
                    "status": model_status,
                })
            })
            .collect::<Vec<_>>();
        json!({
            "supported": self.supported(),
            "runtime_available": self.runtime_available(),
            "model_id": selected_spec.id,
            "model_name": selected_spec.name,
            "revision": selected_spec.revision,
            "download_bytes": selected_spec.download_bytes,
            "enabled": config.enabled,
            "installed": selected_installed,
            "running": running,
            "running_model_id": running_model_id,
            "status": status,
            "port": config.port,
            "base_url": config.port.map(|port| format!("http://127.0.0.1:{port}/v1")),
            "pid": state.child.as_ref().map(Child::id),
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "prompt_cache_bytes": 1_000_000_000_u64,
            "memory_policy": "macos_managed",
            "last_error": state.last_error,
            "operation": operation.map(|operation| operation.action.as_str()),
            "operation_model_id": operation.map(|operation| operation.model_id.as_str()),
            "stage": operation.map(|operation| operation.stage.as_str()),
            "progress": operation.map(|operation| operation.progress),
            "downloaded_bytes": operation.and_then(|operation| operation.downloaded_bytes),
            "total_bytes": operation.and_then(|operation| operation.total_bytes),
            "throughput_bytes_per_second": operation.and_then(|operation| operation.throughput_bytes_per_second),
            "eta_seconds": operation.and_then(|operation| operation.eta_seconds),
            "models": models,
        })
    }

    pub fn install(
        &self,
        model_id: &str,
        mut progress: impl FnMut(&str, u8, &str),
    ) -> io::Result<()> {
        self.require_runtime()?;
        let spec = *model_spec(model_id)?;
        self.set_operation(
            Some(OperationState::new("install", "downloading", spec.id, 5)),
            None,
        );
        let result = (|| {
            if self.install_marker_valid(&spec) {
                self.update_operation("verifying", 82, None, None, None, None);
                progress("verify", 82, "checking the existing pinned model");
                verify_model(&self.model_root(&spec), &spec)?;
                return Ok(());
            }
            let model_root = self.model_root(&spec);
            let already_downloaded = model_downloaded_bytes(&model_root, &spec);
            preflight_free_space(
                &self.root,
                spec.download_bytes.saturating_sub(already_downloaded),
            )?;
            fs::create_dir_all(&model_root)?;
            progress(
                "download",
                download_phase_progress(already_downloaded, spec.download_bytes),
                &format!("downloading {}", spec.name),
            );
            let status = self.download_model(&spec, &mut progress)?;
            if !status.success() {
                return Err(io::Error::other(format!(
                    "{} download exited with {status}; see {}",
                    spec.name,
                    self.log_path.display()
                )));
            }
            self.update_operation("verifying", 86, None, None, None, None);
            progress(
                "verify",
                86,
                &format!(
                    "verifying the immutable {} weights and tokenizer",
                    spec.name
                ),
            );
            verify_model(&model_root, &spec)?;
            write_install_marker(&model_root, &spec)?;
            Ok(())
        })();
        match result {
            Ok(()) => {
                self.set_operation(None, None);
                Ok(())
            }
            Err(error) => {
                self.set_operation(None, Some(error.to_string()));
                Err(error)
            }
        }
    }

    pub fn start(
        &self,
        model_id: &str,
        mut progress: impl FnMut(&str, u8, &str),
    ) -> io::Result<()> {
        let _lifecycle = self
            .lifecycle
            .lock()
            .expect("local AI lifecycle lock poisoned");
        self.release_requested.store(false, Ordering::Release);
        self.require_runtime()?;
        let spec = *model_spec(model_id)?;
        if !self.install_marker_valid(&spec) {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                format!("install {} before starting it", spec.name),
            ));
        }
        self.inspect_exit();
        {
            let state = self.state.lock().expect("local AI state lock poisoned");
            if state.child.is_some() && state.running_model_id.as_deref() == Some(spec.id) {
                drop(state);
                self.desired_running.store(true, Ordering::Release);
                self.update_config(true, None, Some(spec.id))?;
                return Ok(());
            }
        }
        if self
            .state
            .lock()
            .expect("local AI state lock poisoned")
            .child
            .is_some()
        {
            self.stop_child()?;
        } else {
            // A prior daemon may have exited after recording its exact MLX
            // child but before it could cleanly stop it. Reclaim that owned
            // process immediately so two Lemma model servers can never
            // coexist, even across a locald replacement.
            reclaim_owned_process(&self.process_marker_path, self.runtime.as_ref())?;
        }
        self.set_operation(
            Some(OperationState::new("start", "starting", spec.id, 91)),
            None,
        );
        let result = (|| {
            let port = self.available_port()?;
            fs::create_dir_all(self.root.join("huggingface/hub"))?;
            progress(
                "start",
                91,
                &format!("loading {} into Apple unified memory", spec.name),
            );
            let runtime = self.require_runtime()?;
            let log = process_log(&self.log_path)?;
            let stderr = log.try_clone()?;
            let model_root = self.model_root(&spec);
            let chat_template = chat_template_override(&model_root, &spec)?;
            let mut command = Command::new(&runtime.python);
            command
                .args(["-m", "mlx_lm", "server", "--model"])
                .arg(&model_root)
                .args(["--host", "127.0.0.1", "--port", &port.to_string()])
                .args(["--allowed-origins", ""])
                .args([
                    "--temp",
                    "0.6",
                    "--top-p",
                    "0.95",
                    "--top-k",
                    "20",
                    "--min-p",
                    "0",
                    "--max-tokens",
                    MAX_OUTPUT_TOKENS_ARG,
                    "--chat-template-args",
                    r#"{"enable_thinking":true}"#,
                ])
                .args([
                    "--decode-concurrency",
                    DECODE_CONCURRENCY,
                    "--prompt-concurrency",
                    "1",
                ])
                .args([
                    "--prompt-cache-size",
                    "2",
                    "--prompt-cache-bytes",
                    PROMPT_CACHE_BYTES,
                ])
                .current_dir(&self.root)
                .env("PYTHONPATH", &runtime.python_path)
                .env("HF_HOME", self.root.join("huggingface"))
                .env("HF_HUB_OFFLINE", "1")
                .env("HF_HUB_DISABLE_TELEMETRY", "1")
                .env("TRANSFORMERS_OFFLINE", "1")
                .stdin(Stdio::null())
                .stdout(Stdio::from(log))
                .stderr(Stdio::from(stderr));
            if let Some(chat_template) = chat_template {
                command.args(["--chat-template", &chat_template]);
            }
            configure_process_group(&mut command);
            let mut child = command.spawn()?;
            if let Err(error) = self.record_process(&child, &runtime, &spec) {
                let _ = terminate_process_group(&mut child);
                return Err(error);
            }
            {
                let mut state = self.state.lock().expect("local AI state lock poisoned");
                state.child = Some(child);
                state.running_model_id = Some(spec.id.to_owned());
            }
            if let Err(error) = self.wait_ready(port, &spec) {
                let _ = self.stop_child();
                return Err(error);
            }
            if self.release_requested.load(Ordering::Acquire) {
                let _ = self.stop_child();
                return Err(io::Error::new(
                    io::ErrorKind::Interrupted,
                    "MLX startup was cancelled while Lemma was quitting",
                ));
            }
            self.desired_running.store(true, Ordering::Release);
            self.update_config(true, Some(port), Some(spec.id))?;
            Ok(())
        })();
        match result {
            Ok(()) => {
                self.set_operation(None, None);
                Ok(())
            }
            Err(error) => {
                self.desired_running.store(false, Ordering::Release);
                self.set_operation(None, Some(error.to_string()));
                Err(error)
            }
        }
    }

    pub fn start_selected(&self, progress: impl FnMut(&str, u8, &str)) -> io::Result<()> {
        let model_id = self.selected_model_id();
        self.start(&model_id, progress)
    }

    pub fn stop(&self) -> io::Result<()> {
        self.release_requested.store(true, Ordering::Release);
        let _lifecycle = self
            .lifecycle
            .lock()
            .expect("local AI lifecycle lock poisoned");
        self.desired_running.store(false, Ordering::Release);
        let model_id = self
            .state
            .lock()
            .expect("local AI state lock poisoned")
            .running_model_id
            .clone()
            .unwrap_or_else(|| self.selected_model_id());
        self.set_operation(
            Some(OperationState::new("stop", "stopping", &model_id, 95)),
            None,
        );
        let result = self.stop_child();
        self.set_operation(None, result.as_ref().err().map(ToString::to_string));
        result
    }

    pub fn disable(&self) -> io::Result<()> {
        self.stop()?;
        self.update_config(false, None, None)
    }

    pub fn delete(&self, model_id: &str) -> io::Result<bool> {
        self.release_requested.store(true, Ordering::Release);
        let _lifecycle = self
            .lifecycle
            .lock()
            .expect("local AI lifecycle lock poisoned");
        let spec = *model_spec(model_id)?;
        self.inspect_exit();
        let was_selected = self.is_selected(spec.id);
        let is_running = self
            .state
            .lock()
            .expect("local AI state lock poisoned")
            .running_model_id
            .as_deref()
            == Some(spec.id);
        self.set_operation(
            Some(OperationState::new("delete", "deleting", spec.id, 50)),
            None,
        );
        let result = (|| {
            if is_running {
                self.desired_running.store(false, Ordering::Release);
                self.stop_child()?;
            }
            let model_root = self.model_root(&spec);
            match fs::remove_dir_all(&model_root) {
                Ok(()) => {}
                Err(error) if error.kind() == io::ErrorKind::NotFound => {}
                Err(error) => return Err(error),
            }
            if was_selected {
                let replacement = MODEL_SPECS
                    .iter()
                    .find(|candidate| self.install_marker_valid(candidate))
                    .unwrap_or(&MODEL_SPECS[0]);
                self.update_config(false, None, Some(replacement.id))?;
            }
            if spec.id == BONSAI_ID {
                remove_if_present(&self.legacy_marker_path)?;
            }
            Ok(())
        })();
        match result {
            Ok(()) => {
                self.set_operation(None, None);
                Ok(was_selected)
            }
            Err(error) => {
                self.set_operation(None, Some(error.to_string()));
                Err(error)
            }
        }
    }

    pub fn needs_recovery(&self) -> bool {
        self.inspect_exit();
        self.desired_running.load(Ordering::Acquire)
            && self
                .selected_spec()
                .is_ok_and(|spec| self.install_marker_valid(spec))
            && self
                .state
                .lock()
                .expect("local AI state lock poisoned")
                .child
                .is_none()
    }

    pub fn owns_base_url(&self, base_url: &str) -> bool {
        self.base_url().is_some_and(|local| {
            local
                .trim_end_matches('/')
                .eq_ignore_ascii_case(base_url.trim_end_matches('/'))
        })
    }

    fn selected_spec(&self) -> io::Result<&'static ModelSpec> {
        model_spec(&self.selected_model_id())
    }

    fn model_root(&self, spec: &ModelSpec) -> PathBuf {
        self.root.join("models").join(spec.slug)
    }

    fn require_runtime(&self) -> io::Result<MlxRuntime> {
        if !self.supported() {
            return Err(io::Error::new(
                io::ErrorKind::Unsupported,
                "local MLX AI is available only on Apple Silicon Macs",
            ));
        }
        self.runtime.clone().ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::NotFound,
                "this Lemma runtime does not contain the optional MLX packages",
            )
        })
    }

    fn install_marker_valid(&self, spec: &ModelSpec) -> bool {
        install_marker_valid(&self.model_root(spec), spec)
    }

    fn update_config(
        &self,
        enabled: bool,
        port: Option<u16>,
        selected_model_id: Option<&str>,
    ) -> io::Result<()> {
        let mut config = self.config.lock().expect("local AI config lock poisoned");
        config.schema_version = CONFIG_SCHEMA_VERSION;
        config.enabled = enabled;
        if port.is_some() {
            config.port = port;
        }
        if let Some(model_id) = selected_model_id {
            model_spec(model_id)?;
            config.selected_model_id = model_id.to_owned();
        }
        write_private_atomic(&self.config_path, &serde_json::to_vec_pretty(&*config)?)
    }

    fn available_port(&self) -> io::Result<u16> {
        let configured = self
            .config
            .lock()
            .expect("local AI config lock poisoned")
            .port;
        if let Some(port) = configured {
            if TcpListener::bind((Ipv4Addr::LOCALHOST, port)).is_ok() {
                return Ok(port);
            }
        }
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))?;
        listener.local_addr().map(|address| address.port())
    }

    fn download_model(
        &self,
        spec: &ModelSpec,
        progress: &mut impl FnMut(&str, u8, &str),
    ) -> io::Result<ExitStatus> {
        let runtime = self.require_runtime()?;
        let log = process_log(&self.log_path)?;
        let stderr = log.try_clone()?;
        let model_root = self.model_root(spec);
        let mut command = Command::new(&runtime.python);
        command
            .args(["-m", "huggingface_hub.cli.hf", "download", spec.id])
            .args(spec.files.iter().map(|file| file.name))
            .args(["--revision", spec.revision, "--local-dir"])
            .arg(&model_root)
            .args(["--max-workers", "4"])
            .env("PYTHONPATH", &runtime.python_path)
            .env("HF_HOME", self.root.join("huggingface"))
            .env("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
            .env("HF_HUB_DISABLE_TELEMETRY", "1")
            .env_remove("HF_TOKEN")
            .env_remove("HUGGING_FACE_HUB_TOKEN")
            .stdin(Stdio::null())
            .stdout(Stdio::from(log))
            .stderr(Stdio::from(stderr));
        configure_process_group(&mut command);
        let mut child = command.spawn()?;
        let initial_bytes = model_downloaded_bytes(&model_root, spec);
        let started = Instant::now();
        let mut last_reported = u64::MAX;
        loop {
            let downloaded = model_downloaded_bytes(&model_root, spec);
            if downloaded != last_reported {
                last_reported = downloaded;
                let elapsed = started.elapsed().as_secs_f64();
                let transferred = downloaded.saturating_sub(initial_bytes);
                let throughput = (elapsed >= 1.0 && transferred > 0)
                    .then(|| (transferred as f64 / elapsed).round() as u64);
                let eta = throughput
                    .filter(|throughput| *throughput > 0)
                    .map(|throughput| {
                        spec.download_bytes
                            .saturating_sub(downloaded)
                            .div_ceil(throughput)
                    });
                let phase_progress = download_phase_progress(downloaded, spec.download_bytes);
                self.update_operation(
                    "downloading",
                    phase_progress,
                    Some(downloaded),
                    Some(spec.download_bytes),
                    throughput,
                    eta,
                );
                progress(
                    "download",
                    phase_progress,
                    &format!("downloading {}", spec.name),
                );
            }
            if let Some(status) = child.try_wait()? {
                return Ok(status);
            }
            thread::sleep(DOWNLOAD_POLL_INTERVAL);
        }
    }

    fn wait_ready(&self, port: u16, spec: &ModelSpec) -> io::Result<()> {
        let client = Client::builder()
            .connect_timeout(Duration::from_secs(2))
            .timeout(Duration::from_secs(180))
            .no_proxy()
            .build()
            .map_err(io::Error::other)?;
        let deadline = Instant::now() + Duration::from_secs(240);
        let url = format!("http://127.0.0.1:{port}/v1/chat/completions");
        let model = path_text(&self.model_root(spec).canonicalize()?)?;
        let mut last_error = None;
        while Instant::now() < deadline {
            if self.release_requested.load(Ordering::Acquire) {
                return Err(io::Error::new(
                    io::ErrorKind::Interrupted,
                    "MLX startup was cancelled",
                ));
            }
            if let Some(exit) = self.take_exit()? {
                return Err(io::Error::other(format!(
                    "MLX server exited with {exit}; see {}",
                    self.log_path.display()
                )));
            }
            match client
                .post(&url)
                .json(&json!({
                    "model": model,
                    "messages": [{"role":"user","content":"Reply OK without calling a tool."}],
                    "tools": [{
                        "type": "function",
                        "function": {
                            "name": "local_ai_readiness",
                            "description": "Unused readiness probe for the local tool-calling path.",
                            "parameters": {
                                "type": "object",
                                "properties": {},
                                "additionalProperties": false
                            }
                        }
                    }],
                    "max_tokens": 1,
                    "temperature": 0.0,
                    "stream": false,
                }))
                .send()
            {
                Ok(response) if response.status().is_success() => {
                    let payload = response.json::<Value>().map_err(io::Error::other)?;
                    if payload
                        .get("choices")
                        .and_then(Value::as_array)
                        .is_some_and(|choices| !choices.is_empty())
                    {
                        return Ok(());
                    }
                    last_error = Some(io::Error::new(
                        io::ErrorKind::InvalidData,
                        "MLX server returned no completion choices",
                    ));
                }
                Ok(response) => {
                    last_error = Some(io::Error::other(format!(
                        "MLX readiness returned HTTP {}",
                        response.status().as_u16()
                    )));
                }
                Err(error) => last_error = Some(io::Error::other(error)),
            }
            thread::sleep(Duration::from_millis(500));
        }
        Err(last_error.unwrap_or_else(|| {
            io::Error::new(io::ErrorKind::TimedOut, "MLX server readiness timed out")
        }))
    }

    fn stop_child(&self) -> io::Result<()> {
        let child = {
            let mut state = self.state.lock().expect("local AI state lock poisoned");
            state.running_model_id = None;
            state.child.take()
        };
        let result = match child {
            Some(mut child) => terminate_process_group(&mut child),
            None => Ok(()),
        };
        if result.is_ok() {
            remove_if_present(&self.process_marker_path)?;
        }
        result
    }

    fn inspect_exit(&self) {
        let exit = self.take_exit().ok().flatten();
        if let Some(exit) = exit {
            let mut state = self.state.lock().expect("local AI state lock poisoned");
            state.last_error = Some(format!("MLX server exited with {exit}"));
            state.running_model_id = None;
        }
    }

    fn take_exit(&self) -> io::Result<Option<String>> {
        let mut state = self.state.lock().expect("local AI state lock poisoned");
        let Some(child) = state.child.as_mut() else {
            return Ok(None);
        };
        let Some(status) = child.try_wait()? else {
            return Ok(None);
        };
        state.child = None;
        state.running_model_id = None;
        remove_if_present(&self.process_marker_path)?;
        Ok(Some(status.to_string()))
    }

    fn set_operation(&self, operation: Option<OperationState>, error: Option<String>) {
        let mut state = self.state.lock().expect("local AI state lock poisoned");
        state.operation = operation;
        state.last_error = error;
    }

    fn update_operation(
        &self,
        stage: &str,
        progress: u8,
        downloaded_bytes: Option<u64>,
        total_bytes: Option<u64>,
        throughput_bytes_per_second: Option<u64>,
        eta_seconds: Option<u64>,
    ) {
        let mut state = self.state.lock().expect("local AI state lock poisoned");
        if let Some(operation) = state.operation.as_mut() {
            operation.stage = stage.to_owned();
            operation.progress = progress;
            operation.downloaded_bytes = downloaded_bytes;
            operation.total_bytes = total_bytes;
            operation.throughput_bytes_per_second = throughput_bytes_per_second;
            operation.eta_seconds = eta_seconds;
        }
    }

    fn record_process(
        &self,
        child: &Child,
        runtime: &MlxRuntime,
        spec: &ModelSpec,
    ) -> io::Result<()> {
        let identity = process_identity(child.id())?;
        let expected = runtime.python.canonicalize()?;
        if Path::new(&identity.executable).canonicalize()? != expected {
            return Err(io::Error::other(
                "MLX process executable did not match the app-owned runtime",
            ));
        }
        let marker = ProcessMarker {
            schema_version: PROCESS_MARKER_SCHEMA_VERSION,
            model_id: spec.id.into(),
            revision: spec.revision.into(),
            pid: child.id(),
            executable: identity.executable,
            start_identity: identity.start_identity,
        };
        write_private_atomic(
            &self.process_marker_path,
            &serde_json::to_vec_pretty(&marker)?,
        )
    }
}

impl OperationState {
    fn new(action: &str, stage: &str, model_id: &str, progress: u8) -> Self {
        Self {
            action: action.to_owned(),
            stage: stage.to_owned(),
            model_id: model_id.to_owned(),
            progress,
            downloaded_bytes: None,
            total_bytes: None,
            throughput_bytes_per_second: None,
            eta_seconds: None,
        }
    }
}

fn default_model_id() -> String {
    BONSAI_ID.to_owned()
}

fn model_spec(model_id: &str) -> io::Result<&'static ModelSpec> {
    MODEL_SPECS
        .iter()
        .find(|spec| spec.id == model_id)
        .ok_or_else(|| invalid(format!("unknown local AI model {model_id:?}")))
}

fn chat_template_override(model_root: &Path, spec: &ModelSpec) -> io::Result<Option<String>> {
    if spec.id != BONSAI_ID {
        return Ok(None);
    }
    let path = model_root.join("chat_template.jinja");
    let template = fs::read_to_string(&path)?;
    let forced_no_thinking = r#"{{- '<|im_start|>assistant\n<think>\n\n</think>\n\n' }}"#;
    let thinking_aware = r#"{{- '<|im_start|>assistant\n' }}
    {%- if enable_thinking is defined and enable_thinking is false %}
        {{- '<think>\n\n</think>\n\n' }}
    {%- endif %}"#;
    if !template.contains(forced_no_thinking) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!(
                "{} no longer has the reviewed chat template; refusing an unverified serving configuration",
                spec.name
            ),
        ));
    }
    Ok(Some(template.replacen(
        forced_no_thinking,
        thinking_aware,
        1,
    )))
}

fn discover_runtime(host_pack_root: Option<&Path>) -> Option<MlxRuntime> {
    if let (Some(python), Some(python_path)) = (
        std::env::var_os("LEMMA_LOCALD_MLX_PYTHON"),
        std::env::var_os("LEMMA_LOCALD_MLX_PYTHONPATH"),
    ) {
        let runtime = MlxRuntime {
            python: PathBuf::from(python),
            python_path: PathBuf::from(python_path),
        };
        if runtime.python.is_file() && runtime.python_path.is_dir() {
            return Some(runtime);
        }
    }
    let root = host_pack_root?;
    let python = [
        root.join("backend/python/bin/python3"),
        root.join("backend/python/bin/python"),
    ]
    .into_iter()
    .find(|path| path.is_file())?;
    let python_path = root.join("backend/mlx-runtime");
    (python_path.join("mlx_lm").is_dir() && python_path.join("huggingface_hub").is_dir()).then_some(
        MlxRuntime {
            python,
            python_path,
        },
    )
}

fn reclaim_owned_process(path: &Path, runtime: Option<&MlxRuntime>) -> io::Result<()> {
    let raw = match fs::read(path) {
        Ok(raw) if raw.len() <= 64 * 1024 => raw,
        Ok(_) => return Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
    };
    let Ok(marker) = serde_json::from_slice::<ProcessMarker>(&raw) else {
        return Ok(());
    };
    let Some(spec) = MODEL_SPECS
        .iter()
        .find(|spec| spec.id == marker.model_id && spec.revision == marker.revision)
    else {
        return Ok(());
    };
    if !matches!(marker.schema_version, 1 | PROCESS_MARKER_SCHEMA_VERSION) {
        return Ok(());
    }
    let Some(runtime) = runtime else {
        return Ok(());
    };
    let expected = runtime.python.canonicalize()?;
    let identity = match process_identity(marker.pid) {
        Ok(identity) => identity,
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            return remove_if_present(path);
        }
        Err(error) => return Err(error),
    };
    let executable_matches = Path::new(&identity.executable)
        .canonicalize()
        .is_ok_and(|actual| actual == expected)
        && identity.executable == marker.executable;
    if executable_matches
        && identity.start_identity == marker.start_identity
        && model_spec(spec.id).is_ok()
    {
        terminate_verified_process(marker.pid)?;
    }
    remove_if_present(path)
}

fn migrate_legacy_bonsai_marker(root: &Path, legacy_path: &Path) -> io::Result<()> {
    let target_root = root.join("models").join(MODEL_SPECS[0].slug);
    let target_marker = target_root.join(".lemma-install.json");
    if target_marker.exists() || !legacy_path.exists() {
        return Ok(());
    }
    let raw = fs::read(legacy_path)?;
    let Ok(marker) = serde_json::from_slice::<InstallMarker>(&raw) else {
        return Ok(());
    };
    let spec = &MODEL_SPECS[0];
    if matches!(marker.schema_version, 1 | MARKER_SCHEMA_VERSION)
        && marker.model_id == spec.id
        && marker.revision == spec.revision
        && matches!(
            marker.download_bytes,
            BONSAI_DOWNLOAD_BYTES | LEGACY_BONSAI_DOWNLOAD_BYTES
        )
        && marker.weights_sha256 == spec.weights_sha256
        && marker.tokenizer_sha256 == spec.tokenizer_sha256
        && quick_model_check(&target_root, spec)
    {
        write_install_marker(&target_root, spec)?;
        remove_if_present(legacy_path)?;
    }
    Ok(())
}

fn install_marker_valid(root: &Path, spec: &ModelSpec) -> bool {
    let Ok(raw) = fs::read(root.join(".lemma-install.json")) else {
        return false;
    };
    let Ok(marker) = serde_json::from_slice::<InstallMarker>(&raw) else {
        return false;
    };
    marker.schema_version == MARKER_SCHEMA_VERSION
        && marker.model_id == spec.id
        && marker.revision == spec.revision
        && marker.download_bytes == spec.download_bytes
        && marker.weights_sha256 == spec.weights_sha256
        && marker.tokenizer_sha256 == spec.tokenizer_sha256
        && quick_model_check(root, spec)
}

fn write_install_marker(root: &Path, spec: &ModelSpec) -> io::Result<()> {
    let marker = InstallMarker {
        schema_version: MARKER_SCHEMA_VERSION,
        model_id: spec.id.into(),
        revision: spec.revision.into(),
        download_bytes: spec.download_bytes,
        weights_sha256: spec.weights_sha256.into(),
        tokenizer_sha256: spec.tokenizer_sha256.into(),
    };
    write_private_atomic(
        &root.join(".lemma-install.json"),
        &serde_json::to_vec_pretty(&marker)?,
    )
}

fn remove_if_present(path: &Path) -> io::Result<()> {
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

fn read_config(path: &Path) -> io::Result<LocalAiConfig> {
    let mut config = match fs::read(path) {
        Ok(raw) => serde_json::from_slice::<LocalAiConfig>(&raw).map_err(|error| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                format!("invalid local AI configuration: {error}"),
            )
        })?,
        Err(error) if error.kind() == io::ErrorKind::NotFound => LocalAiConfig::default(),
        Err(error) => return Err(error),
    };
    if !matches!(config.schema_version, 1 | CONFIG_SCHEMA_VERSION) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "unsupported local AI configuration schema",
        ));
    }
    model_spec(&config.selected_model_id)?;
    config.schema_version = CONFIG_SCHEMA_VERSION;
    Ok(config)
}

fn quick_model_check(root: &Path, spec: &ModelSpec) -> bool {
    spec.files.iter().all(|file| {
        root.join(file.name)
            .metadata()
            .is_ok_and(|metadata| metadata.is_file() && metadata.len() == file.bytes)
    })
}

fn verify_model(root: &Path, spec: &ModelSpec) -> io::Result<()> {
    if !quick_model_check(root, spec) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!(
                "downloaded {} is incomplete or has unexpected file sizes",
                spec.name
            ),
        ));
    }
    for (name, expected_hash) in [
        ("model.safetensors", spec.weights_sha256),
        ("tokenizer.json", spec.tokenizer_sha256),
    ] {
        let path = root.join(name);
        let actual = file_sha256(&path)?;
        if actual != expected_hash {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("{name} did not match the pinned SHA-256 for {}", spec.name),
            ));
        }
    }
    Ok(())
}

fn model_downloaded_bytes(root: &Path, spec: &ModelSpec) -> u64 {
    let incomplete = root.join(".cache/huggingface/download");
    let incomplete_files = fs::read_dir(incomplete)
        .into_iter()
        .flatten()
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let metadata = entry.metadata().ok()?;
            metadata.is_file().then(|| {
                (
                    entry.file_name().to_string_lossy().into_owned(),
                    metadata.len(),
                )
            })
        })
        .collect::<Vec<_>>();
    spec.files
        .iter()
        .map(|file| {
            let completed = root
                .join(file.name)
                .metadata()
                .ok()
                .filter(|metadata| metadata.is_file())
                .map_or(0, |metadata| metadata.len().min(file.bytes));
            if completed == file.bytes {
                return completed;
            }
            let prefix = format!("{}.", file.name);
            let partial = incomplete_files
                .iter()
                .filter(|(name, _)| name.starts_with(&prefix) && name.ends_with(".incomplete"))
                .map(|(_, bytes)| *bytes)
                .max()
                .unwrap_or(0)
                .min(file.bytes);
            completed.max(partial)
        })
        .sum::<u64>()
        .min(spec.download_bytes)
}

fn directory_payload_bytes(root: &Path, spec: &ModelSpec) -> u64 {
    spec.files
        .iter()
        .filter_map(|file| root.join(file.name).metadata().ok())
        .filter(|metadata| metadata.is_file())
        .map(|metadata| metadata.len())
        .sum()
}

fn download_phase_progress(downloaded: u64, total: u64) -> u8 {
    if total == 0 {
        return 5;
    }
    let ratio = downloaded.min(total) as f64 / total as f64;
    (5.0 + ratio * 75.0).round().clamp(5.0, 80.0) as u8
}

fn file_sha256(path: &Path) -> io::Result<String> {
    let mut file = File::open(path)?;
    let mut digest = Sha256::new();
    let mut buffer = vec![0_u8; 1024 * 1024];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

fn preflight_free_space(path: &Path, required: u64) -> io::Result<()> {
    #[cfg(unix)]
    {
        use std::ffi::CString;
        use std::os::unix::ffi::OsStrExt;
        let path = CString::new(path.as_os_str().as_bytes())
            .map_err(|_| invalid("local AI path contains a NUL byte"))?;
        let mut stats = std::mem::MaybeUninit::<libc::statvfs>::uninit();
        // SAFETY: path is a valid NUL-terminated string and stats is writable.
        if unsafe { libc::statvfs(path.as_ptr(), stats.as_mut_ptr()) } != 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: statvfs initialized stats on success.
        let stats = unsafe { stats.assume_init() };
        let available = u128::from(stats.f_bavail) * u128::from(stats.f_frsize);
        let needed = u128::from(required) + u128::from(DISK_HEADROOM_BYTES);
        if available < needed {
            return Err(io::Error::other(format!(
                "local AI needs at least {} GiB free including operating headroom",
                needed.div_ceil(1024 * 1024 * 1024)
            )));
        }
    }
    #[cfg(not(unix))]
    let _ = (path, required);
    Ok(())
}

fn process_log(path: &Path) -> io::Result<File> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    if path
        .metadata()
        .is_ok_and(|metadata| metadata.len() >= 5 * 1024 * 1024)
    {
        let previous = path.with_extension("previous.log");
        let _ = fs::remove_file(&previous);
        fs::rename(path, previous)?;
    }
    let mut options = OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    options.open(path)
}

#[cfg(unix)]
fn configure_process_group(command: &mut Command) {
    use std::os::unix::process::CommandExt;
    // SAFETY: setsid has no memory-safety preconditions and runs before exec.
    unsafe {
        command.pre_exec(|| {
            if libc::setsid() == -1 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        });
    }
}

#[cfg(not(unix))]
fn configure_process_group(_command: &mut Command) {}

#[cfg(unix)]
fn terminate_process_group(child: &mut Child) -> io::Result<()> {
    let process_group = -(child.id() as i32);
    // SAFETY: the child was started in its own session and process group.
    let result = unsafe { libc::kill(process_group, libc::SIGTERM) };
    if result != 0 {
        let error = io::Error::last_os_error();
        if error.raw_os_error() != Some(libc::ESRCH) {
            return Err(error);
        }
    }
    let deadline = Instant::now() + Duration::from_secs(10);
    while Instant::now() < deadline {
        if child.try_wait()?.is_some() {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(50));
    }
    // SAFETY: same owned process group, now beyond the graceful timeout.
    unsafe { libc::kill(process_group, libc::SIGKILL) };
    child.wait().map(|_| ())
}

#[cfg(not(unix))]
fn terminate_process_group(child: &mut Child) -> io::Result<()> {
    child.kill()?;
    child.wait().map(|_| ())
}

fn write_private_atomic(path: &Path, contents: &[u8]) -> io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::other("local AI file has no parent"))?;
    fs::create_dir_all(parent)?;
    let temporary = parent.join(format!(".local-ai-{}.tmp", std::process::id()));
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
    #[cfg(windows)]
    if path.exists() {
        fs::remove_file(path)?;
    }
    fs::rename(&temporary, path)?;
    Ok(())
}

#[cfg(unix)]
fn set_private_dir(path: &Path) -> io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
}

#[cfg(not(unix))]
fn set_private_dir(_path: &Path) -> io::Result<()> {
    Ok(())
}

fn path_text(path: &Path) -> io::Result<String> {
    path.to_str()
        .map(str::to_owned)
        .ok_or_else(|| invalid("local AI path is not valid UTF-8"))
}

fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message.into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    const TEST_FILES: &[ModelFile] = &[
        ModelFile::new("config.json", 100),
        ModelFile::new("model.safetensors", 200),
    ];

    #[test]
    fn feature_is_compile_time_gated_to_apple_silicon() {
        let root = tempdir().unwrap();
        let manager = LocalAiManager::load(root.path(), None).unwrap();
        assert_eq!(
            manager.supported(),
            cfg!(all(target_os = "macos", target_arch = "aarch64"))
        );
    }

    #[test]
    fn curated_catalog_is_small_pinned_and_16gb_friendly() {
        assert_eq!(MODEL_SPECS.len(), 2);
        assert_eq!(MAX_OUTPUT_TOKENS, 4_096);
        assert_eq!(PROMPT_CACHE_BYTES, "1GB");
        assert_eq!(DECODE_CONCURRENCY, "1");
        for spec in MODEL_SPECS {
            assert_eq!(spec.revision.len(), 40);
            assert!(spec.download_bytes < 3 * 1024 * 1024 * 1024);
            assert!(spec.context_tokens >= 32_768);
            assert!(spec.thinking);
            assert!(spec.tool_calling);
            assert_eq!(
                spec.files.iter().map(|file| file.bytes).sum::<u64>(),
                spec.download_bytes
            );
        }
    }

    #[test]
    fn marker_requires_the_exact_pinned_identity_and_files() {
        let root = tempdir().unwrap();
        let manager = LocalAiManager::load(root.path(), None).unwrap();
        let spec = &MODEL_SPECS[0];
        let model_root = manager.model_root(spec);
        fs::create_dir_all(&model_root).unwrap();
        write_install_marker(&model_root, spec).unwrap();
        assert!(!manager.install_marker_valid(spec));
    }

    #[test]
    fn config_is_opt_in_and_preserves_selected_model() {
        let root = tempdir().unwrap();
        let manager = LocalAiManager::load(root.path(), None).unwrap();
        assert!(!manager.enabled());
        manager
            .update_config(true, Some(49152), Some(QWEN_ID))
            .unwrap();
        let reloaded = LocalAiManager::load(root.path(), None).unwrap();
        assert!(reloaded.enabled());
        assert_eq!(reloaded.selected_model_id(), QWEN_ID);
        assert_eq!(
            reloaded.base_url().as_deref(),
            Some("http://127.0.0.1:49152/v1")
        );
        reloaded.disable().unwrap();
        assert!(!LocalAiManager::load(root.path(), None).unwrap().enabled());
    }

    #[test]
    fn bonsai_template_override_restores_thinking_and_preserves_tools() {
        let root = tempdir().unwrap();
        let template = r#"{{ tools }}
{%- if add_generation_prompt %}
    {{- '<|im_start|>assistant\n<think>\n\n</think>\n\n' }}
{%- endif %}"#;
        fs::write(root.path().join("chat_template.jinja"), template).unwrap();

        let adjusted = chat_template_override(root.path(), &MODEL_SPECS[0])
            .unwrap()
            .unwrap();

        assert!(adjusted.contains("{{ tools }}"));
        assert!(adjusted.contains("enable_thinking is defined and enable_thinking is false"));
        assert!(adjusted.contains(r#"{{- '<|im_start|>assistant\n' }}"#));
        assert!(!adjusted.contains(r#"{{- '<|im_start|>assistant\n<think>\n\n</think>\n\n' }}"#));
        assert!(chat_template_override(root.path(), &MODEL_SPECS[1])
            .unwrap()
            .is_none());
    }

    #[test]
    fn download_progress_counts_completed_and_incomplete_files_once() {
        let root = tempdir().unwrap();
        let spec = ModelSpec {
            id: "test/model",
            slug: "test-model",
            name: "Test",
            description: "Test",
            license: "Test",
            license_url: "https://example.com",
            revision: "0000000000000000000000000000000000000000",
            download_bytes: 300,
            files: TEST_FILES,
            weights_sha256: "",
            tokenizer_sha256: "",
            context_tokens: 1024,
            thinking: false,
            tool_calling: false,
        };
        fs::create_dir_all(root.path().join(".cache/huggingface/download")).unwrap();
        fs::write(root.path().join("config.json"), vec![0_u8; 100]).unwrap();
        fs::write(
            root.path()
                .join(".cache/huggingface/download/model.safetensors.abc.incomplete"),
            vec![0_u8; 75],
        )
        .unwrap();
        assert_eq!(model_downloaded_bytes(root.path(), &spec), 175);
        assert_eq!(download_phase_progress(0, 300), 5);
        assert_eq!(download_phase_progress(300, 300), 80);
    }

    #[test]
    fn process_marker_reclaims_only_the_recorded_process_identity() {
        let root = tempdir().unwrap();
        let marker_path = root.path().join("process.json");
        let mut child = Command::new("/bin/sleep").arg("30").spawn().unwrap();
        let identity = process_identity(child.id()).unwrap();
        let marker = ProcessMarker {
            schema_version: PROCESS_MARKER_SCHEMA_VERSION,
            model_id: BONSAI_ID.into(),
            revision: BONSAI_REVISION.into(),
            pid: child.id(),
            executable: identity.executable,
            start_identity: identity.start_identity,
        };
        write_private_atomic(&marker_path, &serde_json::to_vec_pretty(&marker).unwrap()).unwrap();
        let runtime = MlxRuntime {
            python: PathBuf::from("/bin/sleep"),
            python_path: root.path().to_owned(),
        };

        reclaim_owned_process(&marker_path, Some(&runtime)).unwrap();

        assert!(!marker_path.exists());
        let deadline = Instant::now() + Duration::from_secs(2);
        while Instant::now() < deadline && child.try_wait().unwrap().is_none() {
            thread::sleep(Duration::from_millis(25));
        }
        assert!(child.try_wait().unwrap().is_some());
    }
}
