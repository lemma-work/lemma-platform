//! Serve one pod-app alias with locald's own `app_alias` code, for the
//! WKWebView proof in `desktop/e2e/app_alias_proof`.
//!
//!   cargo run -p lemma-locald --example app_alias_serve -- \
//!       <state dir> <workspace port> <backend port> <canonical app url>
//!
//! Prints the alias URL on one line, then serves until stdin closes.

use std::io::Read;
use std::path::PathBuf;
use std::sync::Arc;

use lemma_locald::app_alias::AppAliasService;

fn main() {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    let [root, workspace, backend, canonical] = arguments.as_slice() else {
        eprintln!("usage: app_alias_serve <state dir> <workspace port> <backend port> <app url>");
        std::process::exit(2);
    };
    let port = |text: &str| -> u16 {
        text.parse().unwrap_or_else(|_| {
            eprintln!("{text} is not a port");
            std::process::exit(2)
        })
    };
    let log: Arc<dyn Fn(&str) + Send + Sync> = Arc::new(|line| eprintln!("{line}"));
    let service = AppAliasService::new(&PathBuf::from(root), port(workspace), port(backend), log)
        .unwrap_or_else(|error| {
            eprintln!("could not start: {error}");
            std::process::exit(1)
        });
    match service.alias_url(canonical) {
        Ok(alias) => println!("{alias}"),
        Err(error) => {
            eprintln!("refused: {error}");
            std::process::exit(1)
        }
    }
    let mut sink = Vec::new();
    let _ = std::io::stdin().read_to_end(&mut sink);
}
