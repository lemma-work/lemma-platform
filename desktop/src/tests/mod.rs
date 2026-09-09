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
///
/// The end of a function is the start of the next item. That used to be
/// `\nfn `, because every function in the shell was a private one in
/// `main.rs`. Modules made them `pub(crate)`, and a marker that no longer
/// matches does not fail -- it silently returns the rest of the file, so a
/// guard asserting "X appears before Y in this function" starts asserting it
/// about the whole shell.
fn function_body<'a>(source: &'a str, signature: &str) -> &'a str {
    const NEXT_ITEM: [&str; 5] = [
        "\nfn ",
        "\nasync fn ",
        "\npub(crate) fn ",
        "\npub(crate) async fn ",
        "\n#[tauri::command",
    ];
    let start = source.find(signature).expect("the function exists");
    let after = start + signature.len();
    let end = NEXT_ITEM
        .iter()
        .filter_map(|marker| source[after..].find(marker))
        .min()
        .map_or(source.len(), |offset| after + offset);
    &source[start..end]
}

/// Every guard's own source, so the guards can be checked.
fn test_sources() -> Vec<(String, String)> {
    let directory = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("src")
        .join("tests");
    let mut files: Vec<std::path::PathBuf> = std::fs::read_dir(&directory)
        .expect("the shell's test directory")
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.extension().is_some_and(|kind| kind == "rs"))
        .collect();
    files.sort();
    assert!(files.len() > 5, "the guards are a directory of modules");
    files
        .iter()
        .map(|path| {
            let name = path
                .file_name()
                .expect("a file name")
                .to_string_lossy()
                .into_owned();
            let source = std::fs::read_to_string(path).expect("a guard module");
            (name, source.replace("\r\n", "\n"))
        })
        .collect()
}

/// `main.rs` was the shell; it is a launcher now.
///
/// Five guards went on reading it by name after the split. Two failed, which
/// is how this was noticed. The other three passed -- scanning 366 lines for
/// a pattern that had moved to one of the twenty-eight modules beside it, and
/// reporting the absence as success. A guard that scans for a pattern
/// anywhere reads `shell_source()`; one that slices around a named function
/// reads the module that owns that function, and fails loudly if it moves.
#[test]
fn no_guard_looks_for_the_shell_inside_main() {
    // Assembled at compile time so this guard does not find itself.
    let needle = concat!("include_str!(\"../main", ".rs\")");
    let offenders: Vec<String> = test_sources()
        .into_iter()
        .filter(|(_, source)| source.contains(needle))
        .map(|(name, _)| name)
        .collect();
    assert!(
        offenders.is_empty(),
        "these read main.rs, which is no longer where the shell lives: {}",
        offenders.join(", "),
    );
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
    let mut unnormalised = Vec::new();
    for (name, source) in test_sources() {
        for (number, line) in source.lines().enumerate() {
            let trimmed = line.trim();
            // A binding, which is what gets searched. An inline
            // `include_str!(..).contains("one line")` cannot span a newline.
            if !trimmed.starts_with("let ") || !trimmed.contains("= include_str!(") {
                continue;
            }
            if !trimmed.contains(r#".replace("\r\n", "\n")"#) {
                unnormalised.push(format!("{name}:{}: {trimmed}", number + 1));
            }
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

/// Every source file of the shell, concatenated.
///
/// `main.rs` was 11,297 lines and the guards below scanned it by name. It is a
/// directory of modules now, and a guard still reading one file would go on
/// passing while covering a fraction of what it used to — so the ones that
/// scan for a pattern anywhere read all of it, and the ones that slice around
/// a named function read the module that owns that function.
///
/// From disk rather than a list of `include_str!`s, for the reason the daemon
/// guard gives: a list somebody maintains is how a module goes unscanned.
pub(crate) fn shell_source() -> String {
    let directory = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut files: Vec<std::path::PathBuf> = std::fs::read_dir(&directory)
        .expect("the shell's source directory")
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.extension().is_some_and(|kind| kind == "rs"))
        .collect();
    files.sort();
    assert!(
        files.len() > 5,
        "the shell is a directory of modules; reading one file means the scan \
         is looking at a fraction of it"
    );
    files
        .iter()
        .map(|path| std::fs::read_to_string(path).expect("a shell module"))
        .collect::<Vec<_>>()
        .join("\n")
        .replace("\r\n", "\n")
}
