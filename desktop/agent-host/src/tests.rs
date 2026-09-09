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
