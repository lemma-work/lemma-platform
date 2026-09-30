//! What Claude Code is started with of the person's own settings: only how
//! it signs in.
//!
//! Claude Code keeps its config folder, because that is where its sign-in is
//! (the keychain entry is named after the folder), and leaves the person's
//! settings out with `settingSources`. What those settings say about signing
//! in -- an `apiKeyHelper`, a Bedrock or Vertex environment -- is handed back
//! as flag settings, read here. Codex and `OpenCode` run in the person's own
//! setup, with Lemma's MCP server and instructions added on top.

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{Map, Value};

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

/// Claude Code's config folder: `CLAUDE_CONFIG_DIR`, or `~/.claude`.
pub(crate) fn claude_config_dir() -> Option<PathBuf> {
    variable("CLAUDE_CONFIG_DIR").or_else(|| {
        variable("HOME")
            .or_else(|| variable("USERPROFILE"))
            .map(|home| home.join(".claude"))
    })
}

fn variable(name: &str) -> Option<PathBuf> {
    std::env::var_os(name)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
}

/// What the person's Claude Code settings in `config` say about signing in,
/// as flag settings, or `None` when they say nothing about it.
pub(crate) fn claude_sign_in(config: &Path) -> Option<Map<String, Value>> {
    let Some(Value::Object(settings)) = read_jsonc(&config.join("settings.json")) else {
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

/// A JSON file that may carry comments and trailing commas, as Claude Code's
/// may.
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
