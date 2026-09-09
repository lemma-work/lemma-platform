use lemma_guestd::{handle_reader, serve_vsock, GuestService, NerdctlEngine};
use std::io::{self, stdin, stdout};

fn main() {
    if let Err(error) = run() {
        eprintln!("lemma-guestd: {error}");
        std::process::exit(1);
    }
}

fn run() -> io::Result<()> {
    match std::env::args().nth(1).as_deref().unwrap_or("serve-vsock") {
        "request" => {
            let mut service = GuestService::<NerdctlEngine>::discover().map_err(error)?;
            // This process ends with the reply below, so nothing it starts on
            // a thread survives to finish. Anything long belongs in the
            // request itself.
            service.set_per_request_process();
            let ok = handle_reader(stdin().lock(), stdout().lock(), &service)?;
            if ok {
                Ok(())
            } else {
                std::process::exit(1)
            }
        }
        "serve-vsock" => {
            let service = GuestService::<NerdctlEngine>::discover().map_err(error)?;
            serve_vsock(&service)
        }
        "--version" | "-V" => {
            println!("lemma-guestd {}", env!("CARGO_PKG_VERSION"));
            Ok(())
        }
        "--help" | "-h" => {
            println!(
                "lemma-guestd {}\n\nUSAGE:\n  lemma-guestd serve-vsock\n  lemma-guestd request",
                env!("CARGO_PKG_VERSION")
            );
            Ok(())
        }
        command => Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("unknown command {command:?}"),
        )),
    }
}

fn error(error: lemma_guestd::GuestError) -> io::Error {
    io::Error::other(error.message)
}
