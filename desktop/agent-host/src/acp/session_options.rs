//! What each coding agent is told about the session Lemma opens, beyond ACP.
//!
//! Lemma is the source of truth for a run's instructions, skills and tools,
//! and a coding agent on somebody's Mac also loads its own: Claude Code reads
//! `~/.claude` (instructions, skills, plugins, hooks, MCP servers), Codex
//! `~/.codex` (`AGENTS.md`, rules, hooks, MCP servers, plugins), `OpenCode`
//! `~/.config/opencode` as well as `~/.claude` and `~/.agents`. Left alone,
//! those compete with Lemma's -- a user skill that drives a browser on the
//! Mac, a hook that rewrites every command, an instruction file for some
//! other project. Claude Code is therefore started by default with flags that
//! leave `~/.claude` out; a person can turn that off ("Use my own skills and
//! settings", `HostConfig::own_settings`), which applies to Claude Code only.
//! Codex and `OpenCode` run on the person's own setup unchanged: Lemma only
//! adds its MCP server, its `LEMMA_*` environment and its instructions.
//!
//! What stays either way: the agent's sign-in, and a bound project's own
//! instructions and settings, which belong to the folder the person chose.
//! See docs/architecture/agent-host.md, "What a coding agent loads".
//!
//! Some tools are withheld whichever way the switch is set, because Lemma
//! offers the same thing and its prompt tells the agent to use Lemma's: the
//! agent's own browser control (the person watches Lemma's browser, not one on
//! their Mac), and its own web search and fetch when the run has Lemma's.

use std::collections::BTreeMap;
use std::path::Path;

use serde_json::{Map, Value, json};

use super::{claude_config_dir, claude_sign_in};
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
/// the person's choice for this agent, and only Claude Code reads it. `lemma_cli` is the `bin/` of Lemma's own
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
        "claude-code" => claude_code(
            spec,
            own_settings,
            &lemma_tools,
            claude_config_dir().as_deref(),
        ),
        "codex" => codex(adapter_environment, &lemma_tools),
        "opencode" => opencode(&lemma_tools),
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
///   their claude.ai connectors load. `plugins: []` adds none of the SDK's.
///   Auto-memory is Claude Code's store of what it learnt across sessions;
///   Lemma has its own. What the person's settings say about signing in (an
///   `apiKeyHelper`, a Bedrock or Vertex environment) comes back as `settings`,
///   the flag layer, which `settingSources` does not govern.
/// - `disallowedTools` merges with the adapter's own list, which already
///   removes `AskUserQuestion` (Lemma asks through `lemma_ask_user`).
fn claude_code(
    spec: &RunSpec,
    own_settings: bool,
    lemma_tools: &LemmaTools,
    claude_config: Option<&Path>,
) -> SessionOptions {
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
        claude_options.insert("plugins".to_owned(), json!([]));
        claude_options.insert(
            "env".to_owned(),
            json!({ "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1" }),
        );
        if let Some(sign_in) = claude_config.and_then(claude_sign_in) {
            claude_options.insert("settings".to_owned(), Value::Object(sign_in));
        }
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
/// bundled browser and computer-use plugins. The person's own skills, hooks,
/// `AGENTS.md` and MCP servers load as they would outside Lemma. The one
/// switch Lemma adds is `web_search`, off when the run has Lemma's own web
/// search, so the agent does not hold two competing search tools.
fn codex(
    adapter_environment: &BTreeMap<String, String>,
    lemma_tools: &LemmaTools,
) -> SessionOptions {
    let mut overrides = Map::new();
    if lemma_tools.web_search {
        overrides.insert("web_search".to_owned(), json!("disabled"));
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

/// `OpenCode`, through its environment switches and a config overlay.
///
/// The person's own skills, instructions and config load as they would outside
/// Lemma. The overlay (`OPENCODE_CONFIG_CONTENT`, merged over the person's own
/// config) only denies `webfetch` when the run has Lemma's own page fetch.
fn opencode(lemma_tools: &LemmaTools) -> SessionOptions {
    let mut permission = Map::new();
    if lemma_tools.web_fetch {
        permission.insert("webfetch".to_owned(), json!("deny"));
    }
    let mut environment = BTreeMap::new();
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

    fn no_web_tools() -> LemmaTools {
        LemmaTools {
            web_search: false,
            web_fetch: false,
        }
    }

    /// Codex and `OpenCode` get none of the switches that hide a person's
    /// own setup, whatever the own-settings choice says.
    #[test]
    fn codex_and_opencode_keep_the_persons_own_setup() {
        let spec: RunSpec = serde_json::from_value(json!({
            "agent_run_id": uuid::Uuid::nil(),
            "conversation_id": uuid::Uuid::nil(),
            "harness_id": uuid::Uuid::nil(),
            "profile_revision": "r",
            "system_prompt": "",
            "prompt": [],
            "run_deadline": "2026-09-25T00:00:00Z",
        }))
        .unwrap();
        for own in [false, true] {
            for key in ["codex", "opencode"] {
                assert_eq!(
                    session_options(key, &BTreeMap::new(), &spec, own, None),
                    SessionOptions::default(),
                    "{key} own_settings={own}"
                );
            }
        }
    }

    #[test]
    fn codex_turns_off_its_web_search_only_when_lemma_serves_one() {
        let options = codex(
            &BTreeMap::new(),
            &LemmaTools {
                web_search: true,
                web_fetch: false,
            },
        );
        let config: Value = serde_json::from_str(&options.environment["CODEX_CONFIG"]).unwrap();
        assert_eq!(config, json!({ "web_search": "disabled" }));
        assert_eq!(
            codex(&BTreeMap::new(), &no_web_tools()),
            SessionOptions::default()
        );
    }

    #[test]
    fn opencode_denies_only_webfetch_when_lemma_serves_one() {
        let options = opencode(&LemmaTools {
            web_search: false,
            web_fetch: true,
        });
        assert_eq!(options.environment.len(), 1, "{:?}", options.environment);
        let overlay: Value =
            serde_json::from_str(&options.environment["OPENCODE_CONFIG_CONTENT"]).unwrap();
        assert_eq!(overlay, json!({ "permission": { "webfetch": "deny" } }));
    }

    #[test]
    fn claude_code_is_given_the_persons_sign_in_as_flag_settings() {
        let home = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(home.path().join(".claude")).unwrap();
        std::fs::write(
            home.path().join(".claude/settings.json"),
            r#"{"apiKeyHelper": "key-helper", "hooks": {"Stop": []}}"#,
        )
        .unwrap();
        let config = home.path().join(".claude");
        let spec: RunSpec = serde_json::from_value(json!({
            "agent_run_id": uuid::Uuid::nil(),
            "conversation_id": uuid::Uuid::nil(),
            "harness_id": uuid::Uuid::nil(),
            "profile_revision": "r",
            "system_prompt": "",
            "prompt": [],
            "run_deadline": "2026-09-25T00:00:00Z",
        }))
        .unwrap();
        let tools = LemmaTools {
            web_search: false,
            web_fetch: false,
        };

        let isolated = claude_code(&spec, false, &tools, Some(&config));
        let options = &isolated.meta.unwrap()["claudeCode"]["options"];
        assert_eq!(options["settings"], json!({ "apiKeyHelper": "key-helper" }));
        assert_eq!(options["plugins"], json!([]));

        let own = claude_code(&spec, true, &tools, Some(&config));
        let options = &own.meta.unwrap()["claudeCode"]["options"];
        assert!(options.get("settings").is_none(), "{options}");
        assert!(options.get("plugins").is_none(), "{options}");
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
