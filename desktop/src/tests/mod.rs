//! The shell's tests, grouped the way the code they cover is grouped.
//!
//! Was one 3,534-line `mod tests` inside an 11,297-line `main.rs`.

use super::*;
use std::fs::File;

mod agent_host;
mod config;
mod diagnostics;
mod locald;
mod misc;
mod navigation;
mod quit;
mod quit_prompt;
mod runtime;
mod splash;
mod update_install;
mod window_placement;
mod windows;

fn capability(name: &str) -> Value {
    let raw = match name {
        "main" => include_str!("../../capabilities/main.json"),
        "control" => include_str!("../../capabilities/control.json"),
        "confirmation" => include_str!("../../capabilities/confirmation.json"),
        "workspace" => include_str!("../../capabilities/workspace.json"),
        other => panic!("unknown capability {other}"),
    };
    serde_json::from_str(raw).expect("capability is valid JSON")
}

/// The source of one free function, for the cases where asserting on
/// behaviour would need a running AppHandle.
fn function_body<'a>(source: &'a str, signature: &str) -> &'a str {
    let start = source.find(signature).expect("the function exists");
    let end = source[start..]
        .find("\nfn ")
        .map_or(source.len(), |offset| start + offset);
    &source[start..end]
}

/// A source-searching test must not depend on how the repo was checked out.
///
/// Git's default on Windows rewrites text files to CRLF, and these tests
/// read their own source and the bundled UI through `include_str!`. A
/// needle containing `\n` then matches nothing -- but only sometimes:
/// `find("\nfn ")` still matches inside `"\r\nfn "`, so most survived and
/// exactly two did not. The failures appear only on the Windows job, which
/// is not in the desktop path filter, so each one costs a push to see.
///
/// Two defences, and this asserts the one that can be asserted from here:
/// every `include_str!` bound for searching normalises on the way in.
/// `.gitattributes` is the other, and means a checkout never has CRLF to
/// normalise.
#[test]
fn every_included_source_is_read_with_normalised_line_endings() {
    let source = include_str!("../main.rs").replace("\r\n", "\n");
    let mut unnormalised = Vec::new();
    for (number, line) in source.lines().enumerate() {
        let trimmed = line.trim();
        // A binding, which is what gets searched. An inline
        // `include_str!(..).contains("one line")` cannot span a newline.
        if !trimmed.starts_with("let ") || !trimmed.contains("= include_str!(") {
            continue;
        }
        if !trimmed.contains(r#".replace("\r\n", "\n")"#) {
            unnormalised.push(format!("main.rs:{}: {trimmed}", number + 1));
        }
    }
    assert!(
        unnormalised.is_empty(),
        "these read included text without normalising, so a needle \
         containing a newline finds nothing on a Windows checkout:\n{}",
        unnormalised.join("\n"),
    );
}

fn granted(name: &str) -> Vec<String> {
    capability(name)["permissions"]
        .as_array()
        .expect("permissions array")
        .iter()
        .filter_map(|value| value.as_str().map(str::to_string))
        .collect()
}

/// Every `invoke("name")` a bundled page makes.
fn invoked_commands(script: &str) -> Vec<String> {
    script
        .split("invoke(\"")
        .skip(1)
        .filter_map(|rest| rest.split('"').next().map(str::to_string))
        .collect()
}
