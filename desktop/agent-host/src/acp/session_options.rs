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
//! their Mac), its own web search and fetch when the run has Lemma's, and
//! Claude Code's own subagents when the run has Lemma's.

use std::collections::BTreeMap;
use std::path::Path;

use agent_client_protocol::schema::v1::McpServer;
use serde_json::{Map, Value, json};

use super::{claude_config_dir, claude_permission_rules, claude_sign_in};
use crate::protocol::RunSpec;

/// The Lemma tool that stands in for an agent's own web search.
const LEMMA_WEB_SEARCH: &str = "lemma_web_search";
/// The Lemma tool that stands in for an agent's own page fetch.
const LEMMA_WEB_FETCH: &str = "lemma_web_fetch";
/// The Lemma tool that stands in for an agent's own subagents.
const LEMMA_SPAWN_SUBAGENT: &str = "lemma_spawn_subagent";

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
    /// Lemma's MCP server travels in `environment` (`CODEX_CONFIG`), so the
    /// session must not name it again in its `mcpServers`.
    pub(crate) mcp_server_in_environment: bool,
}

/// What this run's agent is started with.
///
/// `adapter_environment` is the pinned adapter's own (`agent-adapters.lock.json`),
/// merged into rather than replaced where both set a variable. `own_settings` is
/// the person's choice for this agent, and only Claude Code reads it. `lemma_cli` is the `bin/` of Lemma's own
/// CLI, when this Mac has one for the run, which goes first on the agent's
/// `PATH` so the `lemma` it runs is the release its server is. `mcp_server`
/// is Lemma's own server for the run, when it has one.
pub(crate) fn session_options(
    adapter_key: &str,
    adapter_environment: &BTreeMap<String, String>,
    spec: &RunSpec,
    own_settings: bool,
    lemma_cli: Option<&Path>,
    mcp_server: Option<&McpServer>,
) -> SessionOptions {
    let lemma_tools = LemmaTools::of(spec);
    let mut options = match adapter_key {
        "claude-code" => claude_code(
            spec,
            own_settings,
            &lemma_tools,
            claude_config_dir().as_deref(),
            lemma_cli,
        ),
        "codex" => codex(adapter_environment, &lemma_tools, mcp_server),
        "opencode" => opencode(&lemma_tools),
        _ => SessionOptions::default(),
    };
    // Every harness alike: the process `PATH`, and the shells the agent runs
    // commands in, where the person's startup files would otherwise put a
    // `lemma` they installed themselves back in front (`shell_path`).
    if let Some(bin) = lemma_cli {
        if let Some(path) = path_with(bin, adapter_environment.get("PATH")) {
            options.environment.insert("PATH".to_owned(), path);
        }
        options.environment.extend(super::shell_path::environment(
            bin,
            &super::shell_path::default_folder(),
        ));
    }
    options
}

/// The Lemma tools this run serves that stand in for an agent's own, out of
/// the names it published.
#[derive(Default)]
struct LemmaTools {
    web_search: bool,
    web_fetch: bool,
    spawn_subagent: bool,
}

impl LemmaTools {
    fn of(spec: &RunSpec) -> Self {
        let names = super::scoped_mcp_tool_names(&spec.mcp);
        Self {
            web_search: names.contains(LEMMA_WEB_SEARCH),
            web_fetch: names.contains(LEMMA_WEB_FETCH),
            spawn_subagent: names.contains(LEMMA_SPAWN_SUBAGENT),
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
///   `apiKeyHelper`, a Bedrock or Vertex environment) and their permission
///   rules come back as `settings`, the flag layer, which `settingSources`
///   does not govern (`claude_settings`).
/// - `disallowedTools` merges with the adapter's own list, which already
///   removes `AskUserQuestion` (Lemma asks through `lemma_ask_user`). When the
///   run serves `lemma_spawn_subagent`, Claude Code's own subagent tool goes
///   too: one of its subagents lives and ends inside this session, where
///   Lemma's `lemma_query_subagents` cannot see it and nobody else can follow
///   it, while Lemma's is a conversation of its own that reports back. The
///   tool is `Agent` in current Claude Code, which still reads `Task` as its
///   earlier name; the pinned adapter accepts a Claude Code back to 2.1.0, so
///   both are named.
/// - `env` turns Claude Code's tool search off (`ENABLE_TOOL_SEARCH=false`),
///   whichever way the switch is set. With it on, every MCP tool is held back
///   until the model looks it up by name, so Lemma's tools -- the run's whole
///   reason for being in Lemma -- were in no prompt the model saw, and it
///   reached for the built-in ones it could. Claude Code's own MCP call limit
///   needs nothing: it is longer than the bridge's (`CALL_TIMEOUT`) unless the
///   person set `MCP_TOOL_TIMEOUT`.
/// - `allowedTools` lets the run's own `lemma` CLI run without a permission
///   request (`lemma_cli_rules`), whichever way the switch is set.
fn claude_code(
    spec: &RunSpec,
    own_settings: bool,
    lemma_tools: &LemmaTools,
    claude_config: Option<&Path>,
    lemma_cli: Option<&Path>,
) -> SessionOptions {
    let mut disallowed = vec![json!("mcp__claude-in-chrome")];
    if lemma_tools.web_search {
        disallowed.push(json!("WebSearch"));
    }
    if lemma_tools.web_fetch {
        disallowed.push(json!("WebFetch"));
    }
    if lemma_tools.spawn_subagent {
        disallowed.extend([json!("Agent"), json!("Task")]);
    }
    let mut env = Map::new();
    env.insert("ENABLE_TOOL_SEARCH".to_owned(), json!("false"));
    let mut claude_options = Map::new();
    claude_options.insert("disallowedTools".to_owned(), Value::Array(disallowed));
    if let Some(bin) = lemma_cli {
        claude_options.insert("allowedTools".to_owned(), json!(lemma_cli_rules(bin)));
    }
    // Claude Code's own switch for its browser integration, which also reads
    // a preference `settingSources` does not govern.
    claude_options.insert("extraArgs".to_owned(), json!({ "no-chrome": null }));
    if !own_settings {
        claude_options.insert("settingSources".to_owned(), json!(["project", "local"]));
        claude_options.insert("strictMcpConfig".to_owned(), Value::Bool(true));
        claude_options.insert("plugins".to_owned(), json!([]));
        env.insert("CLAUDE_CODE_DISABLE_AUTO_MEMORY".to_owned(), json!("1"));
        if let Some(settings) = claude_config.and_then(persons_flag_settings) {
            claude_options.insert("settings".to_owned(), Value::Object(settings));
        }
    }
    claude_options.insert("env".to_owned(), Value::Object(env));
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
        mcp_server_in_environment: false,
    }
}

/// What of the person's own Claude Code settings in `config` goes back in as
/// flag settings: how it signs in, and their permission rules.
fn persons_flag_settings(config: &Path) -> Option<Map<String, Value>> {
    let mut settings = claude_sign_in(config).unwrap_or_default();
    if let Some(rules) = claude_permission_rules(config) {
        settings.insert("permissions".to_owned(), Value::Object(rules));
    }
    (!settings.is_empty()).then_some(settings)
}

/// The permission rules that let the run's own `lemma` CLI, in `bin`, run
/// without asking anyone.
///
/// The CLI is signed in as the run (`LEMMA_*`), so it can do only what the
/// run's own Lemma tools can, and those are already answered by the host
/// without a person. Asking before each `lemma` command only stalled the runs
/// nobody watches.
///
/// Each rule names one executable, and never a prefix of the command text: a
/// prefix match is the shell-injection hole Lemma's own session approvals
/// refuse (`approval_reconciliation.record_session_approvals` in the
/// backend). Claude Code matches a `Bash(<command>:*)` rule on whole words, and
/// a compound command (`lemma x; rm -rf ~`, `lemma x && curl ...`) or a
/// substitution needs every part allowed on its own, so neither rule here
/// lets anything but that one program run.
///
/// - `Bash(<bin>/lemma:*)` is the exact binary, by its full path.
/// - `Bash(lemma:*)` is the same binary by name, the way Lemma's prompt tells
///   the agent to run it: this session puts `bin` first on the agent's `PATH`
///   (`session_options`), so the name resolves to that binary and to no other
///   copy, wherever the person installed one.
fn lemma_cli_rules(bin: &Path) -> Vec<String> {
    vec![
        format!("Bash({}:*)", bin.join("lemma").display()),
        "Bash(lemma:*)".to_owned(),
    ]
}

/// Codex, through `CODEX_CONFIG`: `codex-acp` sends it as config overrides on
/// every thread, layered over `~/.codex/config.toml`.
///
/// Merged into the pinned adapter's own value, which already disables Codex's
/// bundled browser and computer-use plugins. The person's own skills, hooks,
/// `AGENTS.md` and MCP servers load as they would outside Lemma. The one
/// switch Lemma adds is `web_search`, off when the run has Lemma's own web
/// search, so the agent does not hold two competing search tools.
///
/// Lemma's own MCP server goes here too, rather than in the session's
/// `mcpServers`, because only here can it carry its limits. Codex gives an MCP
/// call a minute unless the server's entry says otherwise, and Lemma's calls
/// block for longer than that: a named agent answers within five minutes,
/// `lemma_wait_for` and an awaited `lemma_interact_subagent` wait on other
/// work. Cut off, the call failed while the work it started went on. The
/// start is given room too, since the bridge first waits for its relay. `codex-acp` writes the session's
/// `mcpServers` over this config's whole `mcp_servers` table, and Codex
/// applies a dotted `mcp_servers.<name>.tool_timeout_sec` beside that table
/// in no fixed order, so the limits hold only when the entry is this config's
/// alone. A name Codex would not take as a config key stays in `mcpServers`
/// without them.
fn codex(
    adapter_environment: &BTreeMap<String, String>,
    lemma_tools: &LemmaTools,
    mcp_server: Option<&McpServer>,
) -> SessionOptions {
    let mut overrides = Map::new();
    if lemma_tools.web_search {
        overrides.insert("web_search".to_owned(), json!("disabled"));
    }
    let server = mcp_server.and_then(codex_mcp_server);
    let mcp_server_in_environment = server.is_some();
    let mut environment = BTreeMap::new();
    if !overrides.is_empty() || server.is_some() {
        let mut config = adapter_environment
            .get("CODEX_CONFIG")
            .and_then(|raw| serde_json::from_str::<Value>(raw).ok())
            .filter(Value::is_object)
            .unwrap_or_else(|| Value::Object(Map::new()));
        merge(&mut config, &Value::Object(overrides));
        // Replaced whole, not merged: a pinned entry of the same name keeps
        // none of its keys -- an `enabled: false` there would switch Lemma's
        // server off for a run that no longer offers it through ACP either.
        // Any other server in the pinned table stays.
        if let (Some((name, entry)), Some(config)) = (server, config.as_object_mut()) {
            let servers = config
                .entry("mcp_servers".to_owned())
                .or_insert_with(|| Value::Object(Map::new()));
            if !servers.is_object() {
                *servers = Value::Object(Map::new());
            }
            if let Some(servers) = servers.as_object_mut() {
                servers.insert(name, entry);
            }
        }
        environment.insert("CODEX_CONFIG".to_owned(), config.to_string());
    }
    SessionOptions {
        meta: None,
        environment,
        system_prompt_in_meta: false,
        mcp_server_in_environment,
    }
}

/// Lemma's server as a Codex `mcp_servers` entry, with the bridge's own
/// limits: a call may take as long as the bridge lets one take, and starting
/// may take as long as the bridge waits for its relay and then for the
/// listing.
fn codex_mcp_server(server: &McpServer) -> Option<(String, Value)> {
    let McpServer::Stdio(stdio) = server else {
        return None;
    };
    let name_is_a_key = !stdio.name.is_empty()
        && stdio
            .name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-');
    if !name_is_a_key {
        return None;
    }
    let env: Map<String, Value> = stdio
        .env
        .iter()
        .map(|variable| (variable.name.clone(), json!(variable.value)))
        .collect();
    let startup = crate::mcp_bridge::RELAY_WAIT + crate::mcp_bridge::LIST_TIMEOUT;
    Some((
        stdio.name.clone(),
        json!({
            "command": stdio.command.to_string_lossy(),
            "args": stdio.args,
            "env": env,
            "tool_timeout_sec": crate::mcp_bridge::CALL_TIMEOUT.as_secs(),
            "startup_timeout_sec": startup.as_secs(),
        }),
    ))
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
        mcp_server_in_environment: false,
    }
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
        LemmaTools::default()
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
                    session_options(key, &BTreeMap::new(), &spec, own, None, None),
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
                ..LemmaTools::default()
            },
            None,
        );
        let config: Value = serde_json::from_str(&options.environment["CODEX_CONFIG"]).unwrap();
        assert_eq!(config, json!({ "web_search": "disabled" }));
        assert_eq!(
            codex(&BTreeMap::new(), &no_web_tools(), None),
            SessionOptions::default()
        );
    }

    #[test]
    fn opencode_denies_only_webfetch_when_lemma_serves_one() {
        let options = opencode(&LemmaTools {
            web_fetch: true,
            ..LemmaTools::default()
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
        let tools = LemmaTools::default();

        let isolated = claude_code(&spec, false, &tools, Some(&config), None);
        let options = &isolated.meta.unwrap()["claudeCode"]["options"];
        assert_eq!(options["settings"], json!({ "apiKeyHelper": "key-helper" }));
        assert_eq!(options["plugins"], json!([]));

        let own = claude_code(&spec, true, &tools, Some(&config), None);
        let options = &own.meta.unwrap()["claudeCode"]["options"];
        assert!(options.get("settings").is_none(), "{options}");
        assert!(options.get("plugins").is_none(), "{options}");
    }

    fn empty_spec() -> RunSpec {
        serde_json::from_value(json!({
            "agent_run_id": uuid::Uuid::nil(),
            "conversation_id": uuid::Uuid::nil(),
            "harness_id": uuid::Uuid::nil(),
            "profile_revision": "r",
            "system_prompt": "",
            "prompt": [],
            "run_deadline": "2026-09-25T00:00:00Z",
        }))
        .unwrap()
    }

    /// Every `lemma` command used to raise a permission request, and a run
    /// nobody watched waited out each one.
    #[test]
    fn claude_code_may_run_the_runs_own_lemma_cli_without_asking() {
        let bin = Path::new("/Lemma/runtime/releases/7/backend/bin");
        for own in [false, true] {
            let options = claude_code(&empty_spec(), own, &no_web_tools(), None, Some(bin));
            let options = &options.meta.unwrap()["claudeCode"]["options"];
            // Joined the way the host joins it: Windows separates with `\`.
            let exact = format!("Bash({}:*)", bin.join("lemma").display());
            assert_eq!(
                options["allowedTools"],
                json!([exact, "Bash(lemma:*)"]),
                "own_settings={own}"
            );
        }
        // No CLI this Mac took for the run: nothing is allowed by name.
        let options = claude_code(&empty_spec(), false, &no_web_tools(), None, None);
        let options = &options.meta.unwrap()["claudeCode"]["options"];
        assert!(options.get("allowedTools").is_none(), "{options}");
    }

    /// Leaving `~/.claude` out stripped the person's own allowlist; it comes
    /// back as flag settings beside the sign-in, and nothing else of their
    /// `permissions` does.
    #[test]
    fn claude_code_keeps_the_persons_own_permission_rules() {
        let home = tempfile::tempdir().unwrap();
        let config = home.path().join(".claude");
        std::fs::create_dir_all(&config).unwrap();
        std::fs::write(
            config.join("settings.json"),
            r#"{
                // Claude Code's settings may carry comments.
                "apiKeyHelper": "key-helper",
                "permissions": {
                    "allow": ["Bash(npm run test:*)", "Edit(/notes/**)", "Read(//srv/**)"],
                    "deny": ["Bash(git push:*)"],
                    "defaultMode": "bypassPermissions",
                    "additionalDirectories": ["/"]
                },
                "hooks": {"Stop": []},
            }"#,
        )
        .unwrap();

        let options = claude_code(
            &empty_spec(),
            false,
            &no_web_tools(),
            Some(&config),
            Some(Path::new("/pack/bin")),
        );
        let options = &options.meta.unwrap()["claudeCode"]["options"];

        assert_eq!(
            options["settings"],
            json!({
                "apiKeyHelper": "key-helper",
                "permissions": {
                    // `/notes/**` is relative to `~/.claude`; read anywhere
                    // else it would name another folder.
                    "allow": ["Bash(npm run test:*)", "Read(//srv/**)"],
                    "deny": ["Bash(git push:*)"],
                },
            })
        );
        // Lemma's own rule stays its own, apart from the person's.
        let exact = format!("Bash({}:*)", Path::new("/pack/bin").join("lemma").display());
        assert_eq!(options["allowedTools"], json!([exact, "Bash(lemma:*)"]));
    }

    fn claude_options(options: SessionOptions) -> Value {
        options.meta.unwrap()["claudeCode"]["options"].clone()
    }

    /// With tool search on, Lemma's MCP tools were in no prompt the model
    /// saw until it searched for them by name.
    #[test]
    fn claude_code_has_its_tool_search_off_either_way() {
        for own in [false, true] {
            let options =
                claude_options(claude_code(&empty_spec(), own, &no_web_tools(), None, None));
            assert_eq!(
                options["env"]["ENABLE_TOOL_SEARCH"],
                json!("false"),
                "own_settings={own}"
            );
            // Auto-memory stays the person's when they keep their own setup.
            assert_eq!(
                options["env"]
                    .get("CLAUDE_CODE_DISABLE_AUTO_MEMORY")
                    .is_some(),
                !own,
                "own_settings={own}: {options}"
            );
        }
    }

    #[test]
    fn claude_code_gives_up_its_own_subagents_only_for_lemmas() {
        let spec = |names: Value| -> RunSpec {
            let mut spec = empty_spec();
            spec.mcp = json!({ "tool_names": names });
            spec
        };
        for own in [false, true] {
            let served = spec(json!(["lemma_spawn_subagent", "lemma_query_subagents"]));
            let options = claude_options(claude_code(
                &served,
                own,
                &LemmaTools::of(&served),
                None,
                None,
            ));
            let disallowed = options["disallowedTools"].as_array().unwrap();
            assert!(disallowed.contains(&json!("Agent")), "{options}");
            assert!(disallowed.contains(&json!("Task")), "{options}");

            let unserved = spec(json!(["lemma_query_subagents"]));
            let options = claude_options(claude_code(
                &unserved,
                own,
                &LemmaTools::of(&unserved),
                None,
                None,
            ));
            let disallowed = options["disallowedTools"].as_array().unwrap();
            assert!(!disallowed.contains(&json!("Agent")), "{options}");
            assert!(!disallowed.contains(&json!("Task")), "{options}");
        }
    }

    fn bridge(name: &str) -> McpServer {
        McpServer::Stdio(
            agent_client_protocol::schema::v1::McpServerStdio::new(name, "/Lemma/lemma-agent-host")
                .args(vec!["mcp-bridge".to_owned()])
                .env(vec![agent_client_protocol::schema::v1::EnvVariable::new(
                    "LEMMA_AGENT_HOST_BRIDGE",
                    "1",
                )]),
        )
    }

    /// Codex's own minute cut Lemma's blocking calls off while the work they
    /// started went on.
    #[test]
    fn codex_is_given_lemmas_server_with_the_bridges_limits() {
        let pinned = BTreeMap::from([(
            "CODEX_CONFIG".to_owned(),
            json!({ "plugins": { "browser@openai-bundled": { "enabled": false } } }).to_string(),
        )]);
        let options = codex(&pinned, &no_web_tools(), Some(&bridge("lemma_tools")));
        assert!(options.mcp_server_in_environment);
        let config: Value = serde_json::from_str(&options.environment["CODEX_CONFIG"]).unwrap();
        assert_eq!(
            config["mcp_servers"],
            json!({ "lemma_tools": {
                "command": "/Lemma/lemma-agent-host",
                "args": ["mcp-bridge"],
                "env": { "LEMMA_AGENT_HOST_BRIDGE": "1" },
                "tool_timeout_sec": 1800,
                "startup_timeout_sec": 150,
            } })
        );
        assert_eq!(
            config["plugins"]["browser@openai-bundled"]["enabled"],
            json!(false)
        );

        // A pinned entry of the same name is replaced whole -- its `enabled:
        // false` would switch Lemma's server off -- and other servers stay.
        let pinned = BTreeMap::from([(
            "CODEX_CONFIG".to_owned(),
            json!({ "mcp_servers": {
                "lemma_tools": { "enabled": false, "command": "old" },
                "theirs": { "command": "keep" },
            } })
            .to_string(),
        )]);
        let options = codex(&pinned, &no_web_tools(), Some(&bridge("lemma_tools")));
        let config: Value = serde_json::from_str(&options.environment["CODEX_CONFIG"]).unwrap();
        assert!(
            config["mcp_servers"]["lemma_tools"]
                .get("enabled")
                .is_none()
        );
        assert_eq!(
            config["mcp_servers"]["lemma_tools"]["tool_timeout_sec"],
            1800
        );
        assert_eq!(config["mcp_servers"]["theirs"]["command"], "keep");

        // Not a key Codex's config takes: the server stays in the session.
        let options = codex(
            &BTreeMap::new(),
            &no_web_tools(),
            Some(&bridge("lemma tools")),
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
            session_options("cursor", &BTreeMap::new(), &spec, false, None, None),
            SessionOptions::default()
        );
    }
}
