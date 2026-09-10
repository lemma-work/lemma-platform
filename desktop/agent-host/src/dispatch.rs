//! One subcommand, dispatched.

use std::sync::Arc;

use clap::Parser;
use std::time::Duration;

use lemma_agent_host::acp::{AcpDriver, AcpRunRequest, AgentDriver};
use lemma_agent_host::adapters::AdapterManifest;
use lemma_agent_host::config::{HostConfig, HostPaths};
use lemma_agent_host::journal::Journal;
use lemma_agent_host::permissions::PermissionGate;
use lemma_agent_host::protocol::{JsonMap, RunSpec};
use lemma_agent_host::runtime::HostRuntime;
use lemma_agent_host::service::ServiceManager;
use serde_json::Value;
use uuid::Uuid;

use crate::cli::{Cli, Command};
use crate::console::ConsoleCallbacks;
use crate::output::{init_logging, print_value, select_one_target, show_logs, update_targets};

pub(crate) async fn run() -> anyhow::Result<()> {
    let cli = Cli::parse();
    init_logging();
    let paths = cli
        .data_dir
        .map(HostPaths::under)
        .map_or_else(HostPaths::platform_default, Ok)?;
    match cli.command {
        Command::Serve => {
            // Exactly one host per data directory. Two would poll the same
            // pairing, split commands between them, and each mark the machine
            // draining as it exits.
            let _single = paths.lock_single_instance()?;
            let config = HostConfig::load_or_create(&paths)?;
            let runtime = HostRuntime::new(config, paths)?;
            let skip_warmup = std::env::var_os("LEMMA_AGENT_HOST_SKIP_ADAPTER_DOWNLOAD")
                .is_some_and(|value| value == "1");
            let warmup = if skip_warmup {
                tracing::info!("adapter download skipped by request");
                None
            } else {
                Some(runtime.start_adapter_installation()?)
            };
            let result = runtime.serve().await;
            drop(warmup);
            result
        }
        Command::Connect {
            url,
            pairing_code,
            name,
            allow_insecure_http,
        } => {
            let config = HostConfig::load_or_create(&paths)?;
            // Deliberately does not install adapters. Pairing is an HTTP call
            // with a single-use code and needs none of them, but it used to wait
            // for the whole cache to be built first -- which is why connecting
            // took minutes and why nothing appeared to be happening while it
            // did. `serve` warms the cache when the app opens instead, and a
            // harness that is not cached yet reports itself as installing.
            let target = lemma_agent_host::api::TargetClient::pair(
                url,
                &pairing_code,
                &name,
                &config.installation_id,
                allow_insecure_http,
            )
            .await?;
            // One target per Lemma server. Keying this on host_id alone was
            // wrong: the id is issued by the server and changes whenever the
            // host row goes away (revoked, or a workspace rebuilt), so
            // re-pairing left the previous target behind with a credential the
            // server no longer knows. That stale target then failed
            // authentication forever, once per refresh, while the new one
            // worked - the logs filled with 401s that could never recover.
            HostConfig::mutate(&paths, |config| {
                config.targets.retain(|item| {
                    item.base_url != target.base_url && item.host_id != target.host_id
                });
                config.targets.push(target.clone());
                config.validate()?;
                Ok(true)
            })?;
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
            let service = ServiceManager::current().status()?;
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
            let config = HostConfig::load_or_create(&paths)?;
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
            HostConfig::mutate(&paths, |config| {
                config
                    .targets
                    .retain(|item| item.target_id != selected.target_id);
                Ok(true)
            })?;
            Journal::open(&paths.journal)?.remove_target(selected.target_id)?;
            println!(
                "Disconnected {} ({}) and removed its local credential.",
                selected.name, selected.host_id
            );
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
        Command::UninstallService => {
            ServiceManager::current().uninstall()?;
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
                // A smoke run is one shot with no conversation behind it.
                resume_session_id: None,
                workspace_cwd: None,
                context: JsonMap::new(),
                mcp: Value::Null,
                run_deadline: chrono::Utc::now() + chrono::Duration::minutes(10),
                system_prompt_delivery: None,
            };
            let outcome = AcpDriver
                .run(
                    AcpRunRequest {
                        adapter,
                        run_spec: spec,
                        scratch_directory: scratch,
                        mcp_server: None,
                        can_load_session: false,
                        published_config_options: Vec::new(),
                        // A local one-off run has no Lemma target to ask, so a
                        // native permission request is denied immediately
                        // rather than stalling on a prompt nobody will see.
                        permissions: PermissionGate::new(),
                        permission_timeout: Duration::ZERO,
                        // Nothing can ask a smoke run to stop: it has no
                        // control plane, and Ctrl-C takes the whole process.
                        cancel: lemma_agent_host::acp::never_cancelled(),
                        cancel_grace: Duration::ZERO,
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
