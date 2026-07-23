use lemma_runtime::{request, BridgeConfig};
use std::io::{self, stdin, stdout};

fn main() {
    if let Err(error) = run() {
        eprintln!("lemma-runtime: {error}");
        std::process::exit(1);
    }
}

fn run() -> io::Result<()> {
    match std::env::args().nth(1).as_deref() {
        Some("request") => {
            let config = BridgeConfig::discover()?;
            if request(stdin().lock(), stdout().lock(), &config)? {
                Ok(())
            } else {
                std::process::exit(1)
            }
        }
        Some("--version" | "-V") => {
            println!("lemma-runtime {}", env!("CARGO_PKG_VERSION"));
            Ok(())
        }
        Some("--help" | "-h") | None => {
            println!(
                "lemma-runtime {}\n\nUSAGE:\n  lemma-runtime request",
                env!("CARGO_PKG_VERSION")
            );
            Ok(())
        }
        Some(command) => Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("unknown command {command:?}"),
        )),
    }
}
