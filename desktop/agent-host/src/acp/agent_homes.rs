//! The setup a coding agent is started in when it is not using the person's.
//!
//! Switches alone could not keep everything personal out. Codex reads
//! `CODEX_HOME/AGENTS.md`, the execpolicy `rules/`, `notify` and the person's
//! MCP servers whatever it is told, and `OpenCode` reads the global
//! `AGENTS.md`, agents, commands, plugins and MCP servers under
//! `$XDG_CONFIG_HOME/opencode`. What moves those is the folder each reads them
//! from, so this builds a Lemma-private one per agent, under the Agent Host's
//! data directory (`<data>/agent-homes`), holding only what signing in needs:
//!
//! - Codex gets its own `CODEX_HOME` (`agent-homes/codex`): `auth.json` is a
//!   link to the person's, and `config.toml` carries only how Codex signs in
//!   and which provider and model it talks to. Codex writes a refreshed token
//!   by truncating the file it opens, so the write goes through the link and
//!   lands in the person's `auth.json`; a sign-in Codex had to write afresh
//!   (after a sign-out removed the link) is handed back to the person's file
//!   on the next run. When the person keeps Codex's credentials in the
//!   keychain, which Codex files under the home folder's own path, no other
//!   home can reach them, and Codex stays in the person's home.
//! - `OpenCode` gets its own `XDG_CONFIG_HOME` (`agent-homes/opencode/config`).
//!   Every other entry of the person's config folder is linked into it, so
//!   `gh`, `git` and the rest the agent's shell runs still find theirs, and
//!   `opencode/` holds a generated `opencode.json` with only the person's
//!   providers and model. Its sign-in is under `XDG_DATA_HOME`, which does not
//!   move.
//! - Claude Code keeps its config folder, because that is where its sign-in
//!   is (the keychain entry is named after the folder), and leaves the
//!   person's settings out with `settingSources`. What those settings say
//!   about signing in -- an `apiKeyHelper`, a Bedrock or Vertex environment --
//!   is handed back as flag settings, read here.
//!
//! A folder that cannot be built leaves that agent in the person's own setup
//! with the switches it has, and says so in the log, rather than failing the
//! run.

use std::collections::BTreeSet;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use serde_json::{Map, Value};

/// Where each coding agent's own setup lives on this Mac, and where Lemma
/// builds the ones it starts them in instead.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AgentHomes {
    /// `<data>/agent-homes`. `None` builds nothing, and each agent stays in
    /// the person's setup with only its switches set.
    pub root: Option<PathBuf>,
    /// The person's home folder.
    pub home: PathBuf,
    /// Codex's home: `CODEX_HOME`, or `~/.codex`.
    pub codex_home: PathBuf,
    /// Claude Code's config folder: `CLAUDE_CONFIG_DIR`, or `~/.claude`.
    pub claude_config: PathBuf,
    /// `XDG_CONFIG_HOME`, or `~/.config`.
    pub xdg_config: PathBuf,
}

impl AgentHomes {
    /// Each agent's default folders under `home`.
    #[must_use]
    pub fn of_person(home: &Path) -> Self {
        Self {
            root: None,
            home: home.to_path_buf(),
            codex_home: home.join(".codex"),
            claude_config: home.join(".claude"),
            xdg_config: home.join(".config"),
        }
    }

    /// The folders this process's environment names, which are the ones the
    /// agents it starts would otherwise read.
    #[must_use]
    pub fn from_environment() -> Option<Self> {
        let home = variable("HOME").or_else(|| variable("USERPROFILE"))?;
        let mut homes = Self::of_person(&home);
        if let Some(codex) = variable("CODEX_HOME") {
            homes.codex_home = codex;
        }
        if let Some(claude) = variable("CLAUDE_CONFIG_DIR") {
            homes.claude_config = claude;
        }
        if let Some(xdg) = variable("XDG_CONFIG_HOME") {
            homes.xdg_config = xdg;
        }
        Some(homes)
    }

    /// What an Agent Host whose data directory is `data` starts its agents
    /// with: the environment's folders, and `<data>/agent-homes`.
    #[must_use]
    pub fn for_host(data: &Path) -> Option<Self> {
        Self::from_environment().map(|homes| homes.in_folder(data.join("agent-homes")))
    }

    /// The same, building Lemma's own setups under `root`.
    #[must_use]
    pub fn in_folder(mut self, root: PathBuf) -> Self {
        self.root = Some(root);
        self
    }
}

fn variable(name: &str) -> Option<PathBuf> {
    std::env::var_os(name)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
}

/// The keys of the person's Codex `config.toml` a private home keeps: how
/// Codex signs in, and which provider and model it talks to.
const CODEX_SIGN_IN_KEYS: &[&str] = &[
    "model",
    "model_provider",
    "model_providers",
    "cli_auth_credentials_store",
    "forced_login_method",
    "forced_chatgpt_workspace_id",
    "chatgpt_base_url",
    "openai_base_url",
];

/// The Lemma-private `CODEX_HOME` for a run, built fresh from the person's
/// sign-in, or `None` when there is none to build (no root, or credentials
/// only the person's own home can reach).
pub(crate) fn codex_home(homes: &AgentHomes) -> io::Result<Option<PathBuf>> {
    let Some(root) = &homes.root else {
        return Ok(None);
    };
    let config = read_toml(&homes.codex_home.join("config.toml"));
    let person_auth = homes.codex_home.join("auth.json");
    let store = config
        .get("cli_auth_credentials_store")
        .and_then(toml::Value::as_str);
    let keychain_only = match store {
        Some("keyring") => true,
        Some("auto") => !person_auth.is_file(),
        _ => false,
    };
    if keychain_only {
        return Ok(None);
    }
    let private = root.join("codex");
    fs::create_dir_all(&private)?;
    link_sign_in(&private.join("auth.json"), &person_auth)?;
    let kept: toml::Table = config
        .into_iter()
        .filter(|(key, _)| CODEX_SIGN_IN_KEYS.contains(&key.as_str()))
        .collect();
    let body = toml::to_string(&kept).map_err(io::Error::other)?;
    lemma_private_file::write_atomic(
        &private.join("config.toml"),
        format!(
            "# Written by Lemma's Agent Host before each run: only how Codex signs in\n\
             # and which provider and model it uses. Changes here are replaced.\n{body}"
        )
        .as_bytes(),
    )?;
    Ok(Some(private))
}

fn read_toml(path: &Path) -> toml::Table {
    match fs::read_to_string(path) {
        Ok(text) => text.parse::<toml::Table>().unwrap_or_else(|error| {
            tracing::warn!(
                path = %path.display(),
                %error,
                "could not read Codex's config; its sign-in settings are not carried over"
            );
            toml::Table::new()
        }),
        Err(_) => toml::Table::new(),
    }
}

/// `link` made a link to the person's `target`, and kept that way.
///
/// A plain file where the link should be is a sign-in Codex wrote after
/// removing the link (signing out does that), so it is the newest one and
/// goes to the person's file before the link is put back.
fn link_sign_in(link: &Path, target: &Path) -> io::Result<()> {
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)?;
    }
    match fs::symlink_metadata(link) {
        Ok(meta) if meta.file_type().is_symlink() => {
            if fs::read_link(link)? == target {
                return Ok(());
            }
        }
        Ok(meta) if meta.is_file() => {
            if lemma_private_file::replace(link, target).is_err() {
                fs::copy(link, target)?;
                fs::remove_file(link)?;
            }
        }
        Ok(_) => {
            return Err(io::Error::other(format!(
                "{} is neither a file nor a link",
                link.display()
            )));
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(error),
    }
    replace_with_link(link, target, false)
}

/// Put a link to `target` at `link`, atomically where a link already is.
fn replace_with_link(link: &Path, target: &Path, directory: bool) -> io::Result<()> {
    let name = link
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default();
    let temporary = link.with_file_name(format!(".{name}.{}.link", uuid::Uuid::new_v4().simple()));
    symlink(target, &temporary, directory)?;
    fs::rename(&temporary, link).inspect_err(|_| {
        let _ = fs::remove_file(&temporary);
    })
}

#[cfg(unix)]
fn symlink(target: &Path, link: &Path, _directory: bool) -> io::Result<()> {
    std::os::unix::fs::symlink(target, link)
}

#[cfg(windows)]
fn symlink(target: &Path, link: &Path, directory: bool) -> io::Result<()> {
    if directory {
        std::os::windows::fs::symlink_dir(target, link)
    } else {
        std::os::windows::fs::symlink_file(target, link)
    }
}

#[cfg(not(any(unix, windows)))]
fn symlink(_target: &Path, _link: &Path, _directory: bool) -> io::Result<()> {
    Err(io::Error::from(io::ErrorKind::Unsupported))
}

/// The keys of the person's `OpenCode` config a private one keeps: the
/// providers it signs in to, and the model it uses.
const OPENCODE_SIGN_IN_KEYS: &[&str] = &[
    "provider",
    "model",
    "small_model",
    "enabled_providers",
    "disabled_providers",
];

/// The Lemma-private `XDG_CONFIG_HOME` for an `OpenCode` run, or `None` when
/// there is no root to build it in.
pub(crate) fn opencode_config_home(homes: &AgentHomes) -> io::Result<Option<PathBuf>> {
    let Some(root) = &homes.root else {
        return Ok(None);
    };
    let private = root.join("opencode").join("config");
    if private == homes.xdg_config {
        return Ok(None);
    }
    fs::create_dir_all(&private)?;
    mirror_config_folder(&homes.xdg_config, &private)?;

    let own = homes.xdg_config.join("opencode");
    let mut config = Map::new();
    config.insert(
        "$schema".to_owned(),
        Value::String("https://opencode.ai/config.json".to_owned()),
    );
    // In the order `OpenCode` itself merges them.
    for file in ["config.json", "opencode.json", "opencode.jsonc"] {
        let Some(Value::Object(found)) = read_jsonc(&own.join(file)) else {
            continue;
        };
        for (key, value) in found {
            if OPENCODE_SIGN_IN_KEYS.contains(&key.as_str()) {
                config.insert(key, value);
            }
        }
    }
    let folder = private.join("opencode");
    // A link here would be the person's own folder, the one being left out.
    if fs::symlink_metadata(&folder).is_ok_and(|meta| meta.file_type().is_symlink()) {
        fs::remove_file(&folder)?;
    }
    lemma_private_file::write_atomic(
        &folder.join("opencode.json"),
        &serde_json::to_vec_pretty(&Value::Object(config))?,
    )?;
    Ok(Some(private))
}

/// Every entry of the person's config folder but `opencode`, linked into
/// `private`, and links to entries that have gone removed.
fn mirror_config_folder(person: &Path, private: &Path) -> io::Result<()> {
    let mut names = BTreeSet::new();
    if let Ok(entries) = fs::read_dir(person) {
        for entry in entries.filter_map(Result::ok) {
            let name = entry.file_name();
            if name == "opencode" {
                continue;
            }
            let target = entry.path();
            let link = private.join(&name);
            names.insert(name);
            let current = fs::read_link(&link).ok();
            if current.as_deref() == Some(target.as_path()) {
                continue;
            }
            if current.is_none() && fs::symlink_metadata(&link).is_ok() {
                // Something that is not a link: leave it alone.
                continue;
            }
            replace_with_link(&link, &target, target.is_dir())?;
        }
    }
    for entry in fs::read_dir(private)?.filter_map(Result::ok) {
        let name = entry.file_name();
        let is_link = entry.file_type().is_ok_and(|kind| kind.is_symlink());
        if is_link && !names.contains(&name) {
            let path = entry.path();
            // A link to a folder is removed as a folder on Windows, and a run
            // starting beside this one may have removed it already.
            match fs::remove_file(&path).or_else(|_| fs::remove_dir(&path)) {
                Err(error) if error.kind() != io::ErrorKind::NotFound => return Err(error),
                _ => {}
            }
        }
    }
    Ok(())
}

/// The keys of Claude Code's `settings.json` that say how it signs in.
const CLAUDE_SIGN_IN_KEYS: &[&str] = &[
    "apiKeyHelper",
    "awsAuthRefresh",
    "awsCredentialExport",
    "gcpAuthRefresh",
    "forceLoginMethod",
    "forceLoginOrgUUID",
];

/// The variables in Claude Code's settings `env` that choose and reach its
/// provider.
const CLAUDE_SIGN_IN_PREFIXES: &[&str] = &[
    "ANTHROPIC_",
    "CLAUDE_CODE_USE_",
    "CLAUDE_CODE_SKIP_",
    "CLAUDE_CODE_CLIENT_",
    "AWS_",
    "VERTEX_REGION_",
    "CLOUD_ML_REGION",
    "GOOGLE_",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "NODE_EXTRA_CA_CERTS",
];

/// What the person's Claude Code settings say about signing in, as flag
/// settings, or `None` when they say nothing about it.
pub(crate) fn claude_sign_in(homes: &AgentHomes) -> Option<Map<String, Value>> {
    let Some(Value::Object(settings)) = read_jsonc(&homes.claude_config.join("settings.json"))
    else {
        return None;
    };
    let mut kept = Map::new();
    for (key, value) in settings {
        if CLAUDE_SIGN_IN_KEYS.contains(&key.as_str()) {
            kept.insert(key, value);
        } else if key == "env"
            && let Value::Object(environment) = value
        {
            let environment: Map<String, Value> = environment
                .into_iter()
                .filter(|(name, value)| {
                    value.is_string()
                        && CLAUDE_SIGN_IN_PREFIXES
                            .iter()
                            .any(|prefix| name.starts_with(prefix))
                })
                .collect();
            if !environment.is_empty() {
                kept.insert("env".to_owned(), Value::Object(environment));
            }
        }
    }
    (!kept.is_empty()).then_some(kept)
}

/// A JSON file that may carry comments and trailing commas, as `OpenCode`'s
/// and Claude Code's may.
fn read_jsonc(path: &Path) -> Option<Value> {
    let text = fs::read_to_string(path).ok()?;
    serde_json::from_str(&text)
        .or_else(|_| serde_json::from_str(&strip_jsonc(&text)))
        .ok()
}

/// `text` without comments and without commas that close nothing.
pub(crate) fn strip_jsonc(text: &str) -> String {
    drop_trailing_commas(&drop_comments(text))
}

/// Each string in `text` is copied as it is; outside strings, `keep` decides.
fn outside_strings(
    text: &str,
    mut keep: impl FnMut(char, &mut std::iter::Peekable<std::str::Chars<'_>>, &mut String),
) -> String {
    let mut out = String::with_capacity(text.len());
    let mut chars = text.chars().peekable();
    let mut in_string = false;
    while let Some(c) = chars.next() {
        if in_string {
            out.push(c);
            if c == '\\' {
                if let Some(escaped) = chars.next() {
                    out.push(escaped);
                }
            } else if c == '"' {
                in_string = false;
            }
        } else if c == '"' {
            in_string = true;
            out.push(c);
        } else {
            keep(c, &mut chars, &mut out);
        }
    }
    out
}

fn drop_comments(text: &str) -> String {
    outside_strings(text, |c, chars, out| match (c, chars.peek()) {
        ('/', Some('/')) => {
            for skipped in chars.by_ref() {
                if skipped == '\n' {
                    out.push('\n');
                    break;
                }
            }
        }
        ('/', Some('*')) => {
            chars.next();
            let mut last = ' ';
            for skipped in chars.by_ref() {
                if last == '*' && skipped == '/' {
                    break;
                }
                last = skipped;
            }
        }
        _ => out.push(c),
    })
}

fn drop_trailing_commas(text: &str) -> String {
    outside_strings(text, |c, chars, out| {
        if c == ',' {
            let closes = chars
                .clone()
                .find(|next| !next.is_whitespace())
                .is_some_and(|next| matches!(next, '}' | ']'));
            if closes {
                return;
            }
        }
        out.push(c);
    })
}
