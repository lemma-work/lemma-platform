//! The command line's guards.

use crate::cli::{Cli, Command};
use clap::Parser;

#[test]
fn connect_preserves_url_safe_pairing_codes_and_following_flags() {
    for code in [
        "-fixture_pairing-code",
        "--fixture_pairing-code",
        "_fixture-code",
    ] {
        let cli = Cli::try_parse_from([
            "lemma-agent-host",
            "connect",
            "--url",
            "http://127.0.0.1:8710",
            "--pairing-code",
            code,
            "--allow-insecure-http",
            "--name",
            "Test computer",
        ])
        .expect("URL-safe pairing code must be parsed as a value");
        let Command::Connect {
            pairing_code,
            name,
            allow_insecure_http,
            ..
        } = cli.command
        else {
            panic!("expected connect command");
        };
        assert_eq!(pairing_code, code);
        assert_eq!(name, "Test computer");
        assert!(allow_insecure_http);
    }
}

/// The console streams, rather than showing an answer in bursts.
///
/// Rust line-buffers stdout and an agent's message chunks rarely end in a
/// newline, so without an explicit flush the text sits in the buffer until one
/// arrives or the process exits. Asserted on the source because the property
/// is about the buffer, and a test that captured stdout would be measuring its
/// own harness.
#[test]
fn a_streamed_chunk_reaches_the_terminal_when_it_arrives() {
    let source = include_str!("console.rs").replace("\r\n", "\n");
    let printing = source
        .find(r#"print!("{text}")"#)
        .expect("the console prints the chunk");
    let after = &source[printing..];
    assert!(
        after[..after.len().min(400)].contains("flush()"),
        "a chunk printed and not flushed is a chunk the reader does not see",
    );
}
