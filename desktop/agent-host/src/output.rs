//! Logging, printing, and the small conveniences the CLI shares.

use lemma_agent_host::config::{HostConfig, HostPaths, TargetConfig};
use serde_json::Value;
use std::io::{Read, Seek, SeekFrom};
use tracing_subscriber::EnvFilter;
use uuid::Uuid;

pub(crate) fn init_logging() {
    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("lemma_agent_host=info"));
    tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_writer(std::io::stderr)
        .compact()
        .init();
}

pub(crate) fn print_value(value: &Value, json: bool) {
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

pub(crate) fn select_one_target<'a>(
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

pub(crate) fn update_targets(
    paths: &HostPaths,
    selector: Option<&str>,
    mut update: impl FnMut(&mut TargetConfig),
) -> anyhow::Result<()> {
    HostConfig::mutate(paths, |config| {
        if let Some(selector) = selector {
            let selected_id = select_one_target(config, Some(selector))?.target_id;
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
        Ok(true)
    })?;
    Ok(())
}

pub(crate) async fn show_logs(
    path: &std::path::Path,
    lines: usize,
    follow: bool,
) -> anyhow::Result<()> {
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
