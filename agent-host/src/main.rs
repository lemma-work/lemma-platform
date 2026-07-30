use std::io::{Read, Seek, SeekFrom};
use std::path::PathBuf;
use std::sync::Arc;

use clap::{Parser, Subcommand};
use lemma_agent_host::acp::{AcpCallbacks, AcpDriver, AcpRunRequest, AgentDriver};
use lemma_agent_host::adapters::AdapterManifest;
use lemma_agent_host::config::{HostConfig, HostPaths, TargetConfig};
use lemma_agent_host::journal::Journal;
use lemma_agent_host::protocol::{EventType, JsonMap, RunSpec};
use lemma_agent_host::runtime::HostRuntime;
use lemma_agent_host::service::ServiceManager;
use serde_json::Value;
use tracing_subscriber::EnvFilter;
use url::Url;
use uuid::Uuid;

#[derive(Parser)]
#[command(name = "lemma-agent-host", version, about)]
struct Cli {
    /// Override the platform Agent Host data directory.
    #[arg(long, global = true, env = "LEMMA_AGENT_HOST_DATA_DIR")]
    data_dir: Option<PathBuf>,

    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Run all configured target connections until interrupted.
    Serve,
    /// Pair this machine with a Lemma target using a one-time code.
    Connect {
        #[arg(long)]
        url: Url,
        #[arg(long)]
        pairing_code: String,
        #[arg(long, default_value = "My computer")]
        name: String,
        /// Permit plain HTTP only when the URL is loopback.
        #[arg(long)]
        allow_insecure_http: bool,
    },
    /// Show service, target connectivity, and durable queue state.
    #[command(alias = "list")]
    Status {
        #[arg(long)]
        json: bool,
    },
    /// Revoke a target connection and remove its local device identity.
    Disconnect {
        /// Target UUID or exact configured name. Optional when only one exists.
        #[arg(long)]
        target: Option<String>,
        /// Remove local state even if the remote revocation request fails.
        #[arg(long)]
        force_local: bool,
    },
    /// Start the installed per-user headless service.
    Start,
    /// Stop the installed per-user headless service.
    Stop,
    /// Restart the installed per-user headless service.
    Restart,
    /// Stop accepting new runs while allowing active turns to finish.
    Drain {
        #[arg(long)]
        target: Option<String>,
    },
    /// Resume accepting runs after a drain.
    Resume {
        #[arg(long)]
        target: Option<String>,
    },
    /// Force an ACP capability/model/config refresh.
    Refresh {
        #[arg(long)]
        target: Option<String>,
    },
    /// Print the local Agent Host log.
    Logs {
        #[arg(long, default_value_t = 200)]
        lines: usize,
        #[arg(short, long)]
        follow: bool,
    },
    /// Install and start the platform per-user service.
    InstallService,
    /// Stop and remove the platform per-user service.
    UninstallService,
    /// Discover installed certified agents without contacting Lemma.
    Discover {
        #[arg(long)]
        json: bool,
        /// Launch every ready adapter and report live ACP capabilities/config.
        #[arg(long)]
        probe: bool,
    },
    /// Validate configuration, journal integrity, credentials, and adapters.
    Doctor {
        #[arg(long)]
        json: bool,
        /// Reinstall missing or tampered pinned adapters.
        #[arg(long)]
        repair: bool,
    },
    /// Run a direct local ACP smoke prompt. No Lemma tools are injected.
    Run {
        #[arg(long)]
        agent: String,
        #[arg(long)]
        prompt: String,
        /// Emit the ACP session, every streamed event, and the terminal outcome as NDJSON.
        #[arg(long)]
        json: bool,
    },
    /// Internal run-scoped stdio MCP bridge used by ACP adapters.
    #[command(hide = true)]
    McpBridge {
        #[arg(long)]
        target_id: Uuid,
        #[arg(long)]
        run_id: Uuid,
    },
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();
    init_logging();
    let paths = cli
        .data_dir
        .map(HostPaths::under)
        .map_or_else(HostPaths::platform_default, Ok)?;
    match cli.command {
        Command::Serve => {
            let config = HostConfig::load_or_create(&paths)?;
            HostRuntime::new(config, paths)?.serve().await
        }
        Command::Connect {
            url,
            pairing_code,
            name,
            allow_insecure_http,
        } => {
            let mut config = HostConfig::load_or_create(&paths)?;
            let manifest = AdapterManifest::builtin()?.with_cache_root(paths.adapters.clone());
            manifest.install_cache(&paths.adapters, false)?;
            let target = lemma_agent_host::api::TargetClient::pair(
                url,
                &pairing_code,
                &name,
                &config.installation_id,
                allow_insecure_http,
            )
            .await?;
            config.targets.retain(|item| item.host_id != target.host_id);
            config.targets.push(target.clone());
            config.validate()?;
            config.save(&paths)?;
            Journal::open(&paths.journal)?.register_target(target.target_id)?;
            println!(
                "Connected {} as Agent Host {}.",
                target.base_url, target.host_id
            );
            Ok(())
        }
        Command::Status { json } => {
            let config = HostConfig::load_or_create(&paths)?;
            let journal = Journal::open(&paths.journal)?;
            let statuses = config
                .targets
                .iter()
                .map(|target| {
                    let status = journal.target_status(target.target_id)?;
                    Ok(serde_json::json!({
                        "target_id": target.target_id,
                        "name": target.name,
                        "url": target.base_url,
                        "enabled": target.enabled,
                        "host_id": target.host_id,
                        "journal": status,
                    }))
                })
                .collect::<anyhow::Result<Vec<_>>>()?;
            let service = ServiceManager::current(paths.clone())?.status()?;
            print_value(
                &serde_json::json!({
                    "service": service,
                    "targets": statuses,
                    "data_directory": paths.root,
                }),
                json,
            );
            Ok(())
        }
        Command::Disconnect {
            target,
            force_local,
        } => {
            let mut config = HostConfig::load_or_create(&paths)?;
            let selected = select_one_target(&config, target.as_deref())?.clone();
            let client = lemma_agent_host::api::TargetClient::new(
                selected.clone(),
                config.installation_id.clone(),
            )?;
            if let Err(error) = client.revoke().await {
                if !force_local {
                    return Err(error.into());
                }
                eprintln!(
                    "Warning: remote revocation failed; removing local state because --force-local was supplied: {error}"
                );
            }
            config
                .targets
                .retain(|item| item.target_id != selected.target_id);
            config.save(&paths)?;
            Journal::open(&paths.journal)?.remove_target(selected.target_id)?;
            println!(
                "Disconnected {} ({}) and removed its local credential.",
                selected.name, selected.host_id
            );
            Ok(())
        }
        Command::Start => {
            ServiceManager::current(paths)?.start()?;
            println!("Agent Host service started.");
            Ok(())
        }
        Command::Stop => {
            ServiceManager::current(paths)?.stop()?;
            println!("Agent Host service stopped.");
            Ok(())
        }
        Command::Restart => {
            ServiceManager::current(paths)?.restart()?;
            println!("Agent Host service restarted.");
            Ok(())
        }
        Command::Drain { target } => {
            update_targets(&paths, target.as_deref(), |item| item.draining = true)?;
            println!("Agent Host target(s) are draining.");
            Ok(())
        }
        Command::Resume { target } => {
            update_targets(&paths, target.as_deref(), |item| item.draining = false)?;
            println!("Agent Host target(s) resumed.");
            Ok(())
        }
        Command::Refresh { target } => {
            update_targets(&paths, target.as_deref(), |item| {
                item.refresh_generation = item.refresh_generation.saturating_add(1);
            })?;
            println!("Harness refresh requested.");
            Ok(())
        }
        Command::Logs { lines, follow } => show_logs(&paths.log, lines, follow).await,
        Command::InstallService => {
            AdapterManifest::builtin()?
                .with_cache_root(paths.adapters.clone())
                .install_cache(&paths.adapters, false)?;
            let manager = ServiceManager::current(paths)?;
            manager.install()?;
            println!("Agent Host per-user service installed and started.");
            Ok(())
        }
        Command::UninstallService => {
            ServiceManager::current(paths)?.uninstall()?;
            println!("Agent Host per-user service removed.");
            Ok(())
        }
        Command::Discover { json, probe } => {
            let manifest = AdapterManifest::builtin()?.with_cache_root(paths.adapters.clone());
            if !probe {
                print_value(&serde_json::to_value(manifest.discover())?, json);
                return Ok(());
            }
            let mut results = Vec::new();
            for snapshot in manifest.discover() {
                let probe_result =
                    if snapshot.health == lemma_agent_host::protocol::HarnessHealth::Ready {
                        let adapter = manifest.resolve(&snapshot.harness_key)?;
                        let scratch = paths.root.join("probe").join(&snapshot.harness_key);
                        match tokio::time::timeout(
                            std::time::Duration::from_secs(30),
                            AcpDriver.probe(adapter, scratch),
                        )
                        .await
                        {
                            Ok(Ok(outcome)) => serde_json::json!({
                                "ok": true,
                                "outcome": outcome,
                            }),
                            Ok(Err(error)) => serde_json::json!({
                                "ok": false,
                                "error": error.to_string(),
                            }),
                            Err(_) => serde_json::json!({
                                "ok": false,
                                "error": "ACP probe timed out after 30 seconds",
                            }),
                        }
                    } else {
                        serde_json::json!({
                            "ok": false,
                            "error": snapshot.stale_reason,
                        })
                    };
                results.push(serde_json::json!({
                    "harness": snapshot,
                    "acp_probe": probe_result,
                }));
            }
            print_value(&Value::Array(results), json);
            Ok(())
        }
        Command::Doctor { json, repair } => {
            let config = HostConfig::load_or_create(&paths)?;
            let config_result = config.validate();
            let journal_result =
                Journal::open(&paths.journal).and_then(|journal| journal.integrity_check());
            let manifest = AdapterManifest::builtin()?.with_cache_root(paths.adapters.clone());
            let repair_result = repair
                .then(|| manifest.install_cache(&paths.adapters, true))
                .transpose();
            let repair_ok = repair_result.is_ok();
            let repair_error = repair_result.err().map(|error| error.to_string());
            let harnesses = manifest.discover();
            let ready = harnesses
                .iter()
                .filter(|snapshot| {
                    snapshot.health == lemma_agent_host::protocol::HarnessHealth::Ready
                })
                .count();
            let result = serde_json::json!({
                "ok": config_result.is_ok()
                    && journal_result.is_ok()
                    && repair_ok
                    && ready > 0,
                "config": config_result.err().map(|error| error.to_string()),
                "journal": journal_result.err().map(|error| error.to_string()),
                "repair": repair_error,
                "adapter_manifest_id": manifest.manifest_id,
                "adapter_manifest_sha256": manifest.content_digest(),
                "harnesses_ready": ready,
                "harnesses": harnesses,
                "data_directory": paths.root,
            });
            print_value(&result, json);
            anyhow::ensure!(
                result.get("ok").and_then(Value::as_bool) == Some(true),
                "Agent Host doctor found a problem"
            );
            Ok(())
        }
        Command::Run {
            agent,
            prompt,
            json,
        } => {
            let manifest = AdapterManifest::builtin()?.with_cache_root(paths.adapters.clone());
            let adapter = manifest.resolve(&agent)?;
            let scratch = paths.root.join("smoke").join(Uuid::new_v4().to_string());
            let spec = RunSpec {
                agent_run_id: Uuid::new_v4(),
                conversation_id: Uuid::new_v4(),
                harness_id: Uuid::new_v4(),
                profile_revision: "local-smoke".to_owned(),
                model_name: None,
                config_selections: JsonMap::new(),
                system_prompt: String::new(),
                prompt: vec![serde_json::json!({"type": "text", "text": prompt})],
                context: JsonMap::new(),
                mcp: Value::Null,
                run_deadline: chrono::Utc::now() + chrono::Duration::minutes(10),
            };
            let outcome = AcpDriver
                .run(
                    AcpRunRequest {
                        adapter,
                        run_spec: spec,
                        scratch_directory: scratch,
                        mcp_server: None,
                    },
                    Arc::new(ConsoleCallbacks { json }),
                )
                .await?;
            if json {
                println!(
                    "{}",
                    serde_json::json!({
                        "kind": "outcome",
                        "provider_session_id": outcome.provider_session_id,
                        "state": outcome.state,
                        "stop_reason": outcome.stop_reason,
                    })
                );
            } else {
                eprintln!(
                    "Agent stopped with {} ({:?}).",
                    outcome.stop_reason, outcome.state
                );
            }
            Ok(())
        }
        Command::McpBridge { target_id, run_id } => {
            lemma_agent_host::mcp_bridge::run_bridge(&paths, target_id, run_id).await
        }
    }
}

struct ConsoleCallbacks {
    json: bool,
}

impl AcpCallbacks for ConsoleCallbacks {
    fn before_prompt(&self, provider_session_id: &str) -> anyhow::Result<()> {
        if self.json {
            println!(
                "{}",
                serde_json::json!({
                    "kind": "session",
                    "provider_session_id": provider_session_id,
                })
            );
        } else {
            eprintln!("ACP session: {provider_session_id}");
        }
        Ok(())
    }

    fn event(
        &self,
        event_type: EventType,
        object_id: Option<String>,
        payload: JsonMap,
    ) -> anyhow::Result<()> {
        if self.json {
            println!(
                "{}",
                serde_json::json!({
                    "kind": "event",
                    "event_type": event_type,
                    "object_id": object_id,
                    "payload": payload,
                })
            );
        } else if event_type == EventType::AgentMessageChunk
            && let Some(text) = payload.get("text").and_then(Value::as_str)
        {
            print!("{text}");
        }
        Ok(())
    }
}

fn init_logging() {
    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("lemma_agent_host=info"));
    tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_writer(std::io::stderr)
        .compact()
        .init();
}

fn print_value(value: &Value, json: bool) {
    if json {
        println!(
            "{}",
            serde_json::to_string_pretty(value).expect("value is serializable")
        );
        return;
    }
    match value {
        Value::Array(items) => {
            for item in items {
                println!(
                    "{}",
                    serde_json::to_string_pretty(item).expect("value is serializable")
                );
            }
        }
        _ => println!(
            "{}",
            serde_json::to_string_pretty(value).expect("value is serializable")
        ),
    }
}

fn select_one_target<'a>(
    config: &'a HostConfig,
    selector: Option<&str>,
) -> anyhow::Result<&'a TargetConfig> {
    if let Some(selector) = selector {
        let parsed_id = Uuid::parse_str(selector).ok();
        return config
            .targets
            .iter()
            .find(|target| parsed_id == Some(target.target_id) || target.name == selector)
            .ok_or_else(|| anyhow::anyhow!("target {selector:?} was not found"));
    }
    anyhow::ensure!(
        config.targets.len() == 1,
        "specify --target because {} targets are configured",
        config.targets.len()
    );
    Ok(&config.targets[0])
}

fn update_targets(
    paths: &HostPaths,
    selector: Option<&str>,
    mut update: impl FnMut(&mut TargetConfig),
) -> anyhow::Result<()> {
    let mut config = HostConfig::load_or_create(paths)?;
    if let Some(selector) = selector {
        let selected_id = select_one_target(&config, Some(selector))?.target_id;
        let target = config
            .targets
            .iter_mut()
            .find(|target| target.target_id == selected_id)
            .expect("selected target remains present");
        update(target);
    } else {
        anyhow::ensure!(!config.targets.is_empty(), "no targets are configured");
        for target in &mut config.targets {
            update(target);
        }
    }
    config.validate()?;
    config.save(paths)
}

async fn show_logs(path: &std::path::Path, lines: usize, follow: bool) -> anyhow::Result<()> {
    if !path.exists() {
        println!("No Agent Host log exists yet: {}", path.display());
        return Ok(());
    }
    let bytes = std::fs::read(path)?;
    let text = String::from_utf8_lossy(&bytes);
    let selected = text.lines().rev().take(lines).collect::<Vec<_>>();
    for line in selected.iter().rev() {
        println!("{line}");
    }
    if !follow {
        return Ok(());
    }

    let mut offset = u64::try_from(bytes.len()).unwrap_or(u64::MAX);
    loop {
        tokio::time::sleep(std::time::Duration::from_millis(500)).await;
        let length = path.metadata().map_or(0, |metadata| metadata.len());
        if length < offset {
            offset = 0;
        }
        if length == offset {
            continue;
        }
        let mut file = std::fs::File::open(path)?;
        file.seek(SeekFrom::Start(offset))?;
        let mut appended = Vec::new();
        file.read_to_end(&mut appended)?;
        print!("{}", String::from_utf8_lossy(&appended));
        offset = length;
    }
}
