//! Only how the person signs in comes across from their Claude Code settings.

use std::fs;
use std::path::Path;

use serde_json::{Value, json};

use super::claude_sign_in;
use crate::acp::claude_settings::strip_jsonc;

const MARKER: &str = "PERSONAL_MARKER";

fn write(path: &Path, contents: &str) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, contents).unwrap();
}

#[test]
fn claude_code_carries_only_how_the_person_signs_in() {
    let person = tempfile::tempdir().unwrap();
    let config = person.path().join(".claude");
    assert_eq!(claude_sign_in(&config), None);
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
    let kept = Value::Object(claude_sign_in(&config).unwrap());
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
