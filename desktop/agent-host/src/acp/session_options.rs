//! What each coding agent is told about the session Lemma opens, beyond ACP.
//!
//! Lemma is the source of truth for a run's instructions, skills and tools,
//! and a coding agent on somebody's Mac also loads its own: Claude Code reads
//! `~/.claude` (instructions, skills, plugins, hooks, MCP servers), Codex
//! `~/.codex` (`AGENTS.md`, rules, hooks, MCP servers, plugins), `OpenCode`
//! `~/.config/opencode` as well as `~/.claude` and `~/.agents`. Left alone,
//! those compete with Lemma's -- a user skill that drives a browser on the
//! Mac, a hook that rewrites every command, an instruction file for some
//! other project. So by default each agent is started without them: Claude
//! Code through its own switches, Codex and `OpenCode` in a Lemma-private
//! config folder that carries only their sign-in (`acp::agent_homes`). A
//! person can turn that off per agent ("Use my own skills and settings",
//! `HostConfig::own_settings`), which is the behaviour from before.
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

use super::{AgentHomes, claude_sign_in, codex_home, opencode_config_home};
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
/// `PATH` so the `lemma` it runs is the release its server is. `homes` is where
/// each agent's own setup is, and where Lemma builds the private ones.
pub(crate) fn session_options(
    adapter_key: &str,
    adapter_environment: &BTreeMap<String, String>,
    spec: &RunSpec,
    own_settings: bool,
    lemma_cli: Option<&Path>,
    homes: Option<&AgentHomes>,
) -> SessionOptions {
    let lemma_tools = LemmaTools::of(spec);
    let mut options = match adapter_key {
        "claude-code" => claude_code(spec, own_settings, &lemma_tools, homes),
        "codex" => codex(adapter_environment, own_settings, &lemma_tools, homes),
        "opencode" => opencode(own_settings, &lemma_tools, homes),
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
    homes: Option<&AgentHomes>,
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
        if let Some(sign_in) = homes.and_then(claude_sign_in) {
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

/// Codex, in a Lemma-private `CODEX_HOME` and through `CODEX_CONFIG`, which
/// `codex-acp` sends as config overrides on every thread.
///
/// The private home (`acp::agent_homes::codex_home`) is what leaves out the
/// person's `AGENTS.md`, execpolicy `rules/`, `notify`, profiles, plugins and
/// MCP servers: Codex reads them from its home, with no switch for most. Its
/// `config.toml` carries only the sign-in, provider and model, and its
/// `auth.json` is a link to the person's, so the login and token refreshes are
/// theirs. Codex installs its bundled skills (image generation among them)
/// into it afresh.
///
/// The overrides are merged into the pinned adapter's own value, which already
/// disables Codex's bundled browser and computer-use plugins. On top of that,
/// `features` turns off `hooks`, `apps` (the connectors of the person's
/// `ChatGPT` account), `plugins`, `remote_plugin` (plugins synced from that
/// account) and `memories`; and each skill under `~/.agents/skills` -- which Codex reads
/// from every home, and where an older copy of Lemma's own skills lands when
/// installed by hand -- is switched off by path (`skills.config`), since
/// `skills.include_instructions` would take the bundled ones too. When no
/// private home can be built (the person keeps Codex's credentials in the
/// keychain, which only their own home reaches), Codex stays in theirs and
/// their `~/.codex/skills` are switched off the same way. A bound project's
/// `AGENTS.md` loads either way.
fn codex(
    adapter_environment: &BTreeMap<String, String>,
    own_settings: bool,
    lemma_tools: &LemmaTools,
    homes: Option<&AgentHomes>,
) -> SessionOptions {
    let mut overrides = Map::new();
    let mut environment = BTreeMap::new();
    if lemma_tools.web_search {
        overrides.insert("web_search".to_owned(), json!("disabled"));
    }
    if !own_settings {
        overrides.insert(
            "features".to_owned(),
            json!({
                "hooks": false,
                "apps": false,
                "plugins": false,
                "remote_plugin": false,
                "memories": false,
            }),
        );
        let private = homes.and_then(|homes| match codex_home(homes) {
            Ok(private) => private,
            Err(error) => {
                tracing::warn!(
                    %error,
                    "could not build Codex's Lemma home; it runs in the person's own"
                );
                None
            }
        });
        let skills: Vec<Value> = homes
            .map(|homes| codex_user_skills(homes, private.is_none()))
            .unwrap_or_default()
            .into_iter()
            .map(|path| json!({ "path": path, "enabled": false }))
            .collect();
        if !skills.is_empty() {
            overrides.insert("skills".to_owned(), json!({ "config": skills }));
        }
        if let Some(private) = private {
            environment.insert(
                "CODEX_HOME".to_owned(),
                private.to_string_lossy().into_owned(),
            );
        }
    }
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

/// Every `SKILL.md` the person installed for Codex themselves: under
/// `~/.agents/skills`, and under their Codex home's `skills` when Codex runs
/// in it (`in_own_home`).
///
/// A folder whose name starts with a dot is Codex's own (`.system` holds the
/// bundled skills) and is left alone.
fn codex_user_skills(homes: &AgentHomes, in_own_home: bool) -> Vec<String> {
    let mut folders = Vec::new();
    if in_own_home {
        folders.push(homes.codex_home.join("skills"));
    }
    folders.push(homes.home.join(".agents").join("skills"));
    let mut skills = Vec::new();
    for folder in folders {
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

/// `OpenCode`, in a Lemma-private `XDG_CONFIG_HOME`, and through its
/// environment switches and a config overlay.
///
/// The private config folder (`acp::agent_homes::opencode_config_home`) leaves
/// out everything under the person's `~/.config/opencode` -- `AGENTS.md`,
/// `opencode.json` (MCP servers, plugins, instructions), agents, commands,
/// skills -- keeping only their providers and model. The sign-in is under
/// `XDG_DATA_HOME`, which does not move, and the person's other config folders
/// are linked in. `OPENCODE_DISABLE_EXTERNAL_SKILLS` leaves out the skills
/// under `~/.claude` and `~/.agents`, and `OPENCODE_DISABLE_CLAUDE_CODE`
/// Claude Code's instructions and skills, which `OpenCode` otherwise borrows.
/// The overlay (`OPENCODE_CONFIG_CONTENT`) denies the `skill` tool, which also
/// keeps out any skills in `~/.opencode`, a folder no switch moves.
fn opencode(
    own_settings: bool,
    lemma_tools: &LemmaTools,
    homes: Option<&AgentHomes>,
) -> SessionOptions {
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
        match homes.map(opencode_config_home).transpose() {
            Ok(Some(Some(private))) => {
                environment.insert(
                    "XDG_CONFIG_HOME".to_owned(),
                    private.to_string_lossy().into_owned(),
                );
            }
            Ok(_) => {}
            Err(error) => tracing::warn!(
                %error,
                "could not build OpenCode's Lemma config folder; it reads the person's own"
            ),
        }
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

    /// With nowhere to build a private home, Codex stays in the person's and
    /// their own skills are switched off by path; Codex's bundled ones, under a
    /// dot folder, are not -- image generation is one of them.
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
            Some(&AgentHomes::of_person(home.path())),
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

    /// In its Lemma home, Codex has none of the person's `~/.codex/skills` to
    /// switch off, and still reads `~/.agents/skills` from every home.
    #[cfg(unix)]
    #[test]
    fn codex_in_its_lemma_home_switches_off_what_every_home_reads() {
        let home = tempfile::tempdir().unwrap();
        let data = tempfile::tempdir().unwrap();
        skill(&home.path().join(".codex/skills/browser"));
        skill(&home.path().join(".agents/skills/lemma-builder"));
        let homes = AgentHomes::of_person(home.path()).in_folder(data.path().join("agent-homes"));

        let options = codex(
            &BTreeMap::new(),
            false,
            &LemmaTools {
                web_search: false,
                web_fetch: false,
            },
            Some(&homes),
        );

        let private = Path::new(&options.environment["CODEX_HOME"]);
        assert!(private.starts_with(data.path()), "{}", private.display());
        let config: Value = serde_json::from_str(&options.environment["CODEX_CONFIG"]).unwrap();
        for feature in ["hooks", "apps", "plugins", "remote_plugin", "memories"] {
            assert_eq!(config["features"][feature], json!(false), "{feature}");
        }
        let disabled = config["skills"]["config"].as_array().unwrap();
        assert_eq!(disabled.len(), 1, "{disabled:?}");
        assert!(
            Path::new(disabled[0]["path"].as_str().unwrap())
                .ends_with(".agents/skills/lemma-builder/SKILL.md")
        );

        // And with the person's own settings, their own home, untouched.
        let own = codex(
            &BTreeMap::new(),
            true,
            &LemmaTools {
                web_search: false,
                web_fetch: false,
            },
            Some(&homes),
        );
        assert_eq!(own, SessionOptions::default());
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
        let homes = AgentHomes::of_person(home.path());
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

        let isolated = claude_code(&spec, false, &tools, Some(&homes));
        let options = &isolated.meta.unwrap()["claudeCode"]["options"];
        assert_eq!(options["settings"], json!({ "apiKeyHelper": "key-helper" }));
        assert_eq!(options["plugins"], json!([]));

        let own = claude_code(&spec, true, &tools, Some(&homes));
        let options = &own.meta.unwrap()["claudeCode"]["options"];
        assert!(options.get("settings").is_none(), "{options}");
        assert!(options.get("plugins").is_none(), "{options}");
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
            session_options("cursor", &BTreeMap::new(), &spec, false, None, None),
            SessionOptions::default()
        );
    }
}
