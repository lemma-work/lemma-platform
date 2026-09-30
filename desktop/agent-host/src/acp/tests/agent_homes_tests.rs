//! The private setups a coding agent is started in, built from a fake home
//! with a marker in every personal place: only the sign-in may come across.

use std::fs;
use std::path::Path;

use serde_json::{Value, json};

#[cfg(unix)]
use super::opencode_config_home;
use super::{AgentHomes, claude_sign_in, codex_home};
use crate::acp::agent_homes::strip_jsonc;

const MARKER: &str = "PERSONAL_MARKER";

fn write(path: &Path, contents: &str) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, contents).unwrap();
}

fn homes(person: &Path, root: &Path) -> AgentHomes {
    AgentHomes::of_person(person).in_folder(root.to_path_buf())
}

#[cfg(unix)]
/// Every file under `folder`, links followed only when they stay inside it.
fn own_files(folder: &Path) -> Vec<std::path::PathBuf> {
    let mut found = Vec::new();
    for entry in fs::read_dir(folder).unwrap().filter_map(Result::ok) {
        let kind = entry.file_type().unwrap();
        if kind.is_dir() {
            found.extend(own_files(&entry.path()));
        } else if kind.is_file() {
            found.push(entry.path());
        }
    }
    found
}

#[cfg(unix)]
#[test]
fn codex_gets_a_home_with_the_persons_sign_in_and_nothing_else() {
    let person = tempfile::tempdir().unwrap();
    let data = tempfile::tempdir().unwrap();
    let codex = person.path().join(".codex");
    write(&codex.join("AGENTS.md"), MARKER);
    write(&codex.join("rules/default.rules"), MARKER);
    write(&codex.join("skills/mine/SKILL.md"), MARKER);
    write(&codex.join("auth.json"), r#"{"auth_mode":"apikey"}"#);
    write(
        &codex.join("config.toml"),
        &format!(
            "model = \"gpt-test\"\nmodel_provider = \"corp\"\nnotify = [\"{MARKER}\"]\n\
             model_instructions_file = \"{MARKER}\"\n\
             [model_providers.corp]\nbase_url = \"https://llm.example\"\n\
             [mcp_servers.{MARKER}]\ncommand = \"{MARKER}\"\n\
             [profiles.{MARKER}]\nmodel = \"x\"\n"
        ),
    );

    let private = codex_home(&homes(person.path(), data.path()))
        .unwrap()
        .expect("a private home");

    assert!(private.starts_with(data.path()));
    assert_eq!(
        fs::read_link(private.join("auth.json")).unwrap(),
        codex.join("auth.json")
    );
    let config: toml::Table = fs::read_to_string(private.join("config.toml"))
        .unwrap()
        .parse()
        .unwrap();
    assert_eq!(config["model"].as_str(), Some("gpt-test"));
    assert_eq!(config["model_provider"].as_str(), Some("corp"));
    assert!(config.contains_key("model_providers"));
    for file in own_files(&private) {
        let text = fs::read_to_string(&file).unwrap_or_default();
        assert!(!text.contains(MARKER), "{} carries {text}", file.display());
    }
    assert!(!private.join("AGENTS.md").exists());
    assert!(!private.join("rules").exists());
    assert!(!private.join("skills").exists());

    // Built again for the next run, from the same place.
    assert_eq!(
        codex_home(&homes(person.path(), data.path())).unwrap(),
        Some(private)
    );
}

/// Codex writes a refreshed token by truncating the file it opens, which goes
/// through the link; a sign-in written after the link was removed goes back.
#[cfg(unix)]
#[test]
fn a_sign_in_codex_wrote_in_its_lemma_home_goes_back_to_the_persons() {
    let person = tempfile::tempdir().unwrap();
    let data = tempfile::tempdir().unwrap();
    let homes = homes(person.path(), data.path());
    let private = codex_home(&homes).unwrap().unwrap();
    // No sign-in yet: the link waits for one, and a sign-in lands in the
    // person's file.
    fs::write(private.join("auth.json"), "first").unwrap();
    assert_eq!(
        fs::read_to_string(homes.codex_home.join("auth.json")).unwrap(),
        "first"
    );

    fs::remove_file(private.join("auth.json")).unwrap();
    fs::write(private.join("auth.json"), "second").unwrap();
    codex_home(&homes).unwrap().unwrap();
    assert!(
        fs::symlink_metadata(private.join("auth.json"))
            .unwrap()
            .file_type()
            .is_symlink()
    );
    assert_eq!(
        fs::read_to_string(homes.codex_home.join("auth.json")).unwrap(),
        "second"
    );
}

/// A person who signed in again directly since keeps that newer sign-in.
#[cfg(unix)]
#[test]
fn a_newer_personal_sign_in_is_not_overwritten_by_a_stray_copy() {
    let person = tempfile::tempdir().unwrap();
    let data = tempfile::tempdir().unwrap();
    let homes = homes(person.path(), data.path());
    let private = codex_home(&homes).unwrap().unwrap();
    fs::remove_file(private.join("auth.json")).unwrap();
    fs::write(private.join("auth.json"), "stray").unwrap();
    let personal = homes.codex_home.join("auth.json");
    fs::write(&personal, "newer").unwrap();
    let later = std::time::SystemTime::now() + std::time::Duration::from_secs(60);
    fs::File::options()
        .write(true)
        .open(&personal)
        .unwrap()
        .set_modified(later)
        .unwrap();

    codex_home(&homes).unwrap().unwrap();

    assert!(
        fs::symlink_metadata(private.join("auth.json"))
            .unwrap()
            .file_type()
            .is_symlink()
    );
    assert_eq!(fs::read_to_string(&personal).unwrap(), "newer");
}

/// Codex files keychain credentials under its home's own path, so no other
/// home can reach them; it stays in the person's.
#[test]
fn codex_with_its_credentials_only_in_the_keychain_keeps_the_persons_home() {
    let person = tempfile::tempdir().unwrap();
    let data = tempfile::tempdir().unwrap();
    let config = person.path().join(".codex/config.toml");
    write(&config, "cli_auth_credentials_store = \"keyring\"\n");
    assert_eq!(
        codex_home(&homes(person.path(), data.path())).unwrap(),
        None
    );
    write(&config, "cli_auth_credentials_store = \"auto\"\n");
    assert_eq!(
        codex_home(&homes(person.path(), data.path())).unwrap(),
        None
    );
    // `auto` with a file falls back to the file, which the link reaches.
    write(&person.path().join(".codex/auth.json"), "{}");
    #[cfg(unix)]
    assert!(
        codex_home(&homes(person.path(), data.path()))
            .unwrap()
            .is_some()
    );
    // Nowhere to build one.
    assert_eq!(
        codex_home(&AgentHomes::of_person(person.path())).unwrap(),
        None
    );
}

#[cfg(unix)]
#[test]
fn opencode_gets_a_config_folder_with_the_persons_providers_and_other_tools() {
    let person = tempfile::tempdir().unwrap();
    let data = tempfile::tempdir().unwrap();
    let config = person.path().join(".config");
    write(&config.join("opencode/AGENTS.md"), MARKER);
    write(&config.join("opencode/agent/mine.md"), MARKER);
    write(
        &config.join("opencode/opencode.jsonc"),
        &format!(
            "{{\n  // the person's\n  \"model\": \"corp/model\",\n  \
             \"provider\": {{ \"corp\": {{ \"options\": {{ \"baseURL\": \"https://llm.example\" }} }} }},\n  \
             \"mcp\": {{ \"{MARKER}\": {{ \"type\": \"local\", \"command\": [\"{MARKER}\"] }} }},\n  \
             \"instructions\": [\"{MARKER}\"],\n  \"plugin\": [\"{MARKER}\"],\n}}\n"
        ),
    );
    write(&config.join("gh/hosts.yml"), "github.com: {}\n");

    let private = opencode_config_home(&homes(person.path(), data.path()))
        .unwrap()
        .expect("a private config folder");

    let written: Value =
        serde_json::from_str(&fs::read_to_string(private.join("opencode/opencode.json")).unwrap())
            .unwrap();
    assert_eq!(written["model"], json!("corp/model"));
    assert_eq!(
        written["provider"]["corp"]["options"]["baseURL"],
        json!("https://llm.example")
    );
    assert!(!written.to_string().contains(MARKER), "{written}");
    assert!(!private.join("opencode/AGENTS.md").exists());
    assert!(!private.join("opencode/agent").exists());
    // The person's other tools still find their own config.
    assert_eq!(
        fs::read_link(private.join("gh")).unwrap(),
        config.join("gh")
    );
    assert!(private.join("gh/hosts.yml").is_file());

    // A folder the person removes is unlinked on the next run.
    fs::remove_dir_all(config.join("gh")).unwrap();
    opencode_config_home(&homes(person.path(), data.path())).unwrap();
    assert!(fs::symlink_metadata(private.join("gh")).is_err());
}

#[test]
fn claude_code_carries_only_how_the_person_signs_in() {
    let person = tempfile::tempdir().unwrap();
    let homes = AgentHomes::of_person(person.path());
    assert_eq!(claude_sign_in(&homes), None);
    write(
        &person.path().join(".claude/settings.json"),
        &json!({
            "apiKeyHelper": "~/bin/key",
            "env": {
                "CLAUDE_CODE_USE_BEDROCK": "1",
                "AWS_PROFILE": "work",
                "MARKER_VARIABLE": MARKER,
            },
            "hooks": { "PreToolUse": [MARKER] },
            "enabledPlugins": { MARKER: true },
            "outputStyle": MARKER,
        })
        .to_string(),
    );
    let kept = Value::Object(claude_sign_in(&homes).unwrap());
    assert_eq!(
        kept,
        json!({
            "apiKeyHelper": "~/bin/key",
            "env": { "CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "work" },
        })
    );
}

#[test]
fn jsonc_loses_its_comments_and_trailing_commas_but_not_its_strings() {
    let text = "{\n // note\n \"a\": \"x // y, }\", /* block */ \"b\": [1, 2,],\n}";
    let value: Value = serde_json::from_str(&strip_jsonc(text)).unwrap();
    assert_eq!(value, json!({ "a": "x // y, }", "b": [1, 2] }));
}
