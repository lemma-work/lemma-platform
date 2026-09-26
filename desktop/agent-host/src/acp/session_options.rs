//! What each coding agent is told about the session Lemma opens, beyond ACP.
//!
//! Lemma is the source of truth for a run's instructions, skills and tools,
//! and a coding agent on somebody's Mac also loads its own: Claude Code reads
//! `~/.claude` (instructions, skills, plugins, hooks, MCP servers), Codex
//! `~/.codex`, `OpenCode` `~/.claude` and `~/.agents` as well as its own. Left
//! alone, those compete with Lemma's -- a user skill that drives a browser on
//! the Mac, a hook that rewrites every command, an instruction file for some
//! other project. So by default each agent is started with its own switches
//! set to leave them out. A person can turn that off per agent ("Use my own
//! skills and settings", `HostConfig::own_settings`), which is the behaviour
//! from before.
//!
//! What stays either way: the agent's sign-in. Nothing here moves a config
//! home (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, XDG), because that is where each
//! keeps its credentials. That is also the limit of what can be left out:
//! Codex always reads `~/.codex/AGENTS.md`, and `OpenCode` always reads
//! `~/.config/opencode`, and neither offers a switch that leaves the login in
//! place. See docs/architecture/agent-host.md, "What a coding agent loads".
//!
//! Some tools are withheld whichever way the switch is set, because Lemma
//! offers the same thing and its prompt tells the agent to use Lemma's: the
//! agent's own browser control (the person watches Lemma's browser, not one on
//! their Mac), and its own web search and fetch when the run has Lemma's.

use std::collections::BTreeMap;
use std::path::Path;

use serde_json::{Map, Value, json};

use crate::protocol::RunSpec;

/// The Lemma tool that stands in for an agent's own web search.
const LEMMA_WEB_SEARCH: &str = "lemma_web_search";
/// The Lemma tool that stands in for an agent's own page fetch.
const LEMMA_WEB_FETCH: &str = "lemma_web_fetch";

/// How one run's session is opened, beyond what ACP itself says.
#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) struct SessionOptions {
    /// `_meta` on `session/new` and `session/load`.
    pub(crate) meta: Option<Map<String, Value>>,
    /// Variables set on the adapter process over the pinned adapter's own:
    /// Lemma's, computed from them where they overlap (`CODEX_CONFIG`).
    pub(crate) environment: BTreeMap<String, String>,
    /// The instructions travel in `meta`, so the prompt must not repeat them.
    pub(crate) system_prompt_in_meta: bool,
}

/// What this run's agent is started with.
///
/// `adapter_environment` is the pinned adapter's own (`agent-adapters.lock.json`),
/// merged into rather than replaced where both set a variable. `own_settings` is
/// the person's choice for this agent. `lemma_cli` is the `bin/` of Lemma's own
/// CLI, when this Mac has one for the run, which goes first on the agent's
/// `PATH` so the `lemma` it runs is the release its server is.
pub(crate) fn session_options(
    adapter_key: &str,
    adapter_environment: &BTreeMap<String, String>,
    spec: &RunSpec,
    own_settings: bool,
    lemma_cli: Option<&Path>,
) -> SessionOptions {
    let lemma_tools = LemmaTools::of(spec);
    let mut options = match adapter_key {
        "claude-code" => claude_code(spec, own_settings, &lemma_tools),
        "codex" => codex(
            adapter_environment,
            own_settings,
            &lemma_tools,
            home_directory().as_deref(),
        ),
        "opencode" => opencode(own_settings, &lemma_tools),
        _ => SessionOptions::default(),
    };
    if let Some(bin) = lemma_cli
        && let Some(path) = path_with(bin, adapter_environment.get("PATH"))
    {
        options.environment.insert("PATH".to_owned(), path);
    }
    options
}

/// The web tools Lemma serves this run, out of the names it published.
struct LemmaTools {
    web_search: bool,
    web_fetch: bool,
}

impl LemmaTools {
    fn of(spec: &RunSpec) -> Self {
        let names = super::scoped_mcp_tool_names(&spec.mcp);
        Self {
            web_search: names.contains(LEMMA_WEB_SEARCH),
            web_fetch: names.contains(LEMMA_WEB_FETCH),
        }
    }
}

/// Claude Code, through `claude-agent-acp`'s `_meta` (its `createSession`).
///
/// - `systemPrompt.append` adds Lemma's instructions to Claude Code's own
///   system prompt. They used to open the first user message as a `<system>`
///   block, which put them in the transcript as something the person said,
///   and only on the turn that opened the session. Every run starts its own
///   adapter process, so they are given again, current, on every one.
/// - `claudeCode.options` is passed to the Claude Agent SDK. `settingSources`
///   without `"user"` leaves out `~/.claude`'s instructions, skills, agents,
///   commands, plugins, hooks and settings, and keeps a bound project's own
///   (`"project"`, `"local"`). `strictMcpConfig` keeps only the MCP servers
///   the session names -- Lemma's -- so neither the person's own servers nor
///   their claude.ai connectors load. Auto-memory is Claude Code's store of
///   what it learnt across sessions; Lemma has its own.
/// - `disallowedTools` merges with the adapter's own list, which already
///   removes `AskUserQuestion` (Lemma asks through `lemma_ask_user`).
fn claude_code(spec: &RunSpec, own_settings: bool, lemma_tools: &LemmaTools) -> SessionOptions {
    let mut disallowed = vec![json!("mcp__claude-in-chrome")];
    if lemma_tools.web_search {
        disallowed.push(json!("WebSearch"));
    }
    if lemma_tools.web_fetch {
        disallowed.push(json!("WebFetch"));
    }
    let mut claude_options = Map::new();
    claude_options.insert("disallowedTools".to_owned(), Value::Array(disallowed));
    // Claude Code's own switch for its browser integration, which also reads
    // a preference `settingSources` does not govern.
    claude_options.insert("extraArgs".to_owned(), json!({ "no-chrome": null }));
    if !own_settings {
        claude_options.insert("settingSources".to_owned(), json!(["project", "local"]));
        claude_options.insert("strictMcpConfig".to_owned(), Value::Bool(true));
        claude_options.insert(
            "env".to_owned(),
            json!({ "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1" }),
        );
    }
    let mut meta = Map::new();
    let instructions = spec.system_prompt.trim();
    if !instructions.is_empty() {
        meta.insert("systemPrompt".to_owned(), json!({ "append": instructions }));
    }
    meta.insert(
        "claudeCode".to_owned(),
        json!({ "options": Value::Object(claude_options) }),
    );
    SessionOptions {
        meta: Some(meta),
        environment: BTreeMap::new(),
        system_prompt_in_meta: !instructions.is_empty(),
    }
}

/// Codex, through `CODEX_CONFIG`: `codex-acp` sends it as config overrides on
/// every thread, layered over `~/.codex/config.toml`.
///
/// Merged into the pinned adapter's own value, which already disables Codex's
/// bundled browser and computer-use plugins. Each of the person's own skills
/// -- `~/.codex/skills` and `~/.agents/skills`, which is also where an older
/// copy of Lemma's own skills lands when installed by hand -- is switched off
/// by path (`skills.config`), which leaves Codex's bundled ones (image
/// generation among them) in place; `skills.include_instructions` would have
/// taken those too. `features.hooks` switches off the person's hooks. A bound
/// project's `AGENTS.md` still loads, as Claude Code's project instructions
/// do, and `~/.codex/AGENTS.md` loads regardless: Codex has no switch for it
/// short of another `CODEX_HOME`, which is where the login is.
fn codex(
    adapter_environment: &BTreeMap<String, String>,
    own_settings: bool,
    lemma_tools: &LemmaTools,
    home: Option<&Path>,
) -> SessionOptions {
    let mut overrides = Map::new();
    if lemma_tools.web_search {
        overrides.insert("web_search".to_owned(), json!("disabled"));
    }
    if !own_settings {
        overrides.insert("features".to_owned(), json!({ "hooks": false }));
        let skills: Vec<Value> = home
            .map(codex_user_skills)
            .unwrap_or_default()
            .into_iter()
            .map(|path| json!({ "path": path, "enabled": false }))
            .collect();
        if !skills.is_empty() {
            overrides.insert("skills".to_owned(), json!({ "config": skills }));
        }
    }
    let mut environment = BTreeMap::new();
    if !overrides.is_empty() {
        let mut config = adapter_environment
            .get("CODEX_CONFIG")
            .and_then(|raw| serde_json::from_str::<Value>(raw).ok())
            .filter(Value::is_object)
            .unwrap_or_else(|| Value::Object(Map::new()));
        merge(&mut config, &Value::Object(overrides));
        environment.insert("CODEX_CONFIG".to_owned(), config.to_string());
    }
    SessionOptions {
        meta: None,
        environment,
        system_prompt_in_meta: false,
    }
}

/// Every `SKILL.md` the person installed for Codex themselves.
///
/// A folder whose name starts with a dot is Codex's own (`.system` holds the
/// bundled skills) and is left alone.
fn codex_user_skills(home: &Path) -> Vec<String> {
    let codex_home = std::env::var_os("CODEX_HOME")
        .filter(|value| !value.is_empty())
        .map_or_else(|| home.join(".codex"), std::path::PathBuf::from);
    let mut skills = Vec::new();
    for folder in [codex_home.join("skills"), home.join(".agents/skills")] {
        let Ok(entries) = std::fs::read_dir(&folder) else {
            continue;
        };
        let mut found: Vec<String> = entries
            .filter_map(Result::ok)
            .filter(|entry| !entry.file_name().to_string_lossy().starts_with('.'))
            .map(|entry| entry.path().join("SKILL.md"))
            .filter(|skill| skill.is_file())
            .map(|skill| skill.to_string_lossy().into_owned())
            .collect();
        found.sort();
        skills.extend(found);
    }
    skills
}

/// `OpenCode`, through its environment switches and a config overlay.
///
/// `OPENCODE_DISABLE_EXTERNAL_SKILLS` leaves out the skills under `~/.claude`
/// and `~/.agents`, and `OPENCODE_DISABLE_CLAUDE_CODE` Claude Code's
/// instructions and skills, which `OpenCode` otherwise borrows. The overlay
/// (`OPENCODE_CONFIG_CONTENT`, merged over the person's own config) denies the
/// `skill` tool, the only way to keep `OpenCode`'s own skill folder out without
/// moving `XDG_CONFIG_HOME`; its `AGENTS.md` there still loads.
fn opencode(own_settings: bool, lemma_tools: &LemmaTools) -> SessionOptions {
    let mut permission = Map::new();
    if lemma_tools.web_fetch {
        permission.insert("webfetch".to_owned(), json!("deny"));
    }
    let mut environment = BTreeMap::new();
    if !own_settings {
        permission.insert("skill".to_owned(), json!("deny"));
        environment.insert(
            "OPENCODE_DISABLE_EXTERNAL_SKILLS".to_owned(),
            "1".to_owned(),
        );
        environment.insert("OPENCODE_DISABLE_CLAUDE_CODE".to_owned(), "1".to_owned());
    }
    if !permission.is_empty() {
        environment.insert(
            "OPENCODE_CONFIG_CONTENT".to_owned(),
            json!({ "permission": Value::Object(permission) }).to_string(),
        );
    }
    SessionOptions {
        meta: None,
        environment,
        system_prompt_in_meta: false,
    }
}

/// The `bin/` of the `lemma` CLI Lemma named for this run, if this Mac takes
/// it: the same folder, and the same rule, host execution uses
/// (`host_exec::seatbelt::lemma_cli_root`).
pub(crate) fn lemma_cli_bin(spec: &RunSpec) -> Option<std::path::PathBuf> {
    let raw = spec.mcp.get("lemma_cli").and_then(Value::as_str)?;
    let home = home_directory()?;
    let home = std::fs::canonicalize(&home).unwrap_or(home);
    crate::host_exec::seatbelt::lemma_cli_root(raw, &home).map(|root| root.join("bin"))
}

fn home_directory() -> Option<std::path::PathBuf> {
    std::env::var_os("HOME")
        .filter(|value| !value.is_empty())
        .map(std::path::PathBuf::from)
}

/// `bin` ahead of `path`.
fn path_with(bin: &Path, path: Option<&String>) -> Option<String> {
    let mut entries = vec![bin.to_path_buf()];
    if let Some(path) = path {
        entries.extend(std::env::split_paths(path));
    }
    std::env::join_paths(entries)
        .ok()
        .map(|joined| joined.to_string_lossy().into_owned())
}

/// `overrides` into `base`, objects key by key and everything else replaced.
fn merge(base: &mut Value, overrides: &Value) {
    match (base, overrides) {
        (Value::Object(base), Value::Object(overrides)) => {
            for (key, value) in overrides {
                merge(base.entry(key.clone()).or_insert(Value::Null), value);
            }
        }
        (base, value) => *base = value.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn skill(at: &Path) {
        std::fs::create_dir_all(at).unwrap();
        std::fs::write(at.join("SKILL.md"), "---\nname: x\n---\n").unwrap();
    }

    /// The person's own skills are switched off by path; Codex's bundled ones,
    /// under a dot folder, are not -- image generation is one of them.
    #[test]
    fn codex_leaves_out_the_persons_skills_and_keeps_its_own() {
        let home = tempfile::tempdir().unwrap();
        skill(&home.path().join(".codex/skills/browser"));
        skill(&home.path().join(".codex/skills/.system/imagegen"));
        skill(&home.path().join(".agents/skills/lemma-builder"));
        std::fs::create_dir_all(home.path().join(".codex/skills/not-a-skill")).unwrap();

        let options = codex(
            &BTreeMap::new(),
            false,
            &LemmaTools {
                web_search: false,
                web_fetch: false,
            },
            Some(home.path()),
        );

        let config: Value = serde_json::from_str(&options.environment["CODEX_CONFIG"]).unwrap();
        let disabled: Vec<&str> = config["skills"]["config"]
            .as_array()
            .unwrap()
            .iter()
            .inspect(|entry| assert_eq!(entry["enabled"], json!(false)))
            .map(|entry| entry["path"].as_str().unwrap())
            .collect();
        assert_eq!(disabled.len(), 2, "{disabled:?}");
        assert!(
            disabled
                .iter()
                .any(|path| std::path::Path::new(path).ends_with(".codex/skills/browser/SKILL.md"))
        );
        assert!(disabled.iter().any(|path| {
            std::path::Path::new(path).ends_with(".agents/skills/lemma-builder/SKILL.md")
        }));
        assert!(!disabled.iter().any(|path| path.contains(".system")));
    }

    #[test]
    fn codex_with_its_own_settings_and_no_lemma_web_tools_is_left_alone() {
        let options = codex(
            &BTreeMap::new(),
            true,
            &LemmaTools {
                web_search: false,
                web_fetch: false,
            },
            None,
        );
        assert_eq!(options, SessionOptions::default());
    }

    #[test]
    fn merging_keeps_what_the_pinned_config_sets_beside_it() {
        let mut base = json!({ "features": { "apps": false }, "plugins": { "a": 1 } });
        merge(&mut base, &json!({ "features": { "hooks": false } }));
        assert_eq!(
            base,
            json!({ "features": { "apps": false, "hooks": false }, "plugins": { "a": 1 } })
        );
    }

    #[test]
    fn an_agent_this_host_does_not_know_gets_nothing_extra() {
        let spec: RunSpec = serde_json::from_value(json!({
            "agent_run_id": uuid::Uuid::nil(),
            "conversation_id": uuid::Uuid::nil(),
            "harness_id": uuid::Uuid::nil(),
            "profile_revision": "r",
            "system_prompt": "Be exact.",
            "prompt": [],
            "run_deadline": "2026-09-25T00:00:00Z",
        }))
        .unwrap();
        assert_eq!(
            session_options("cursor", &BTreeMap::new(), &spec, false, None),
            SessionOptions::default()
        );
    }
}
