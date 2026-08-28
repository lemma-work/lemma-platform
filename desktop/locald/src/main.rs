use std::io::{self, BufReader, Write};

use interprocess::local_socket::prelude::*;
use lemma_locald::daemon::Daemon;
use lemma_locald::paths::LocalPaths;
use lemma_locald::protocol::read_bounded_line;
use lemma_locald::PROTOCOL_VERSION;
use serde_json::{json, Value};

fn main() {
    if let Err(error) = run() {
        eprintln!("lemma-locald: {error}");
        std::process::exit(1);
    }
}

fn run() -> io::Result<()> {
    let mut arguments = std::env::args().skip(1);
    match arguments.next().as_deref().unwrap_or("serve") {
        "serve" => serve(),
        // Deliberately never constructs a Daemon: this is what a user reaches
        // for when the daemon is the thing that will not start.
        "reset" => lemma_locald::reset::reset_install(LocalPaths::discover()?),
        "status" => client_request(json!({"cmd": "status", "id": "cli-status"})),
        "ping" => client_request(json!({"cmd": "ping", "id": "cli-ping"})),
        "send" => {
            let raw = arguments.next().ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "send requires one JSON request",
                )
            })?;
            let request: Value = serde_json::from_str(&raw).map_err(|error| {
                io::Error::new(
                    io::ErrorKind::InvalidInput,
                    format!("invalid JSON: {error}"),
                )
            })?;
            client_request(request)
        }
        "--version" | "-V" => {
            println!("lemma-locald {}", env!("CARGO_PKG_VERSION"));
            Ok(())
        }
        "--help" | "-h" => {
            println!(
                "lemma-locald {}\n\nUSAGE:\n  lemma-locald serve\n  lemma-locald reset    destroy this installation's local state\n  lemma-locald status\n  lemma-locald ping\n  lemma-locald send '<json>'",
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

/// Start the daemon, and make sure a failure to *construct* it leaves a record.
///
/// `Daemon::new` does every fallible thing before `serve()` binds anything --
/// token, operator config, infra secrets, host pack, port reservation -- and
/// `write_daemon_log` is a method on the value it failed to produce. So the
/// reason went to stderr, which the desktop shell attaches to a null sink, and
/// the user was told only "lemma-locald exited during startup (exit status: 1)".
fn serve() -> io::Result<()> {
    let paths = LocalPaths::discover()?;
    match Daemon::new(paths.clone()) {
        Ok(daemon) => daemon.serve(),
        Err(error) => {
            let _ = lemma_locald::protocol::append_bounded_daemon_log(
                &paths.log,
                &format!("lemma-locald could not start: {error}"),
            );
            Err(error)
        }
    }
}

fn client_request(request: Value) -> io::Result<()> {
    let paths = LocalPaths::discover()?;
    let token = std::fs::read_to_string(&paths.token)?;
    let stream = LocalSocketStream::connect(paths.socket_name()?)?;
    let (receive, mut send) = stream.split();
    writeln!(
        send,
        "{}",
        json!({"v": PROTOCOL_VERSION, "cmd": "hello", "token": token.trim()})
    )?;
    writeln!(send, "{request}")?;
    send.flush()?;

    let expected_id = request.get("id").cloned();
    let command = request
        .get("cmd")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let mut reader = BufReader::new(receive);
    while let Some(line) = read_bounded_line(&mut reader)? {
        println!("{line}");
        let Ok(event) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        if event.get("id") != expected_id.as_ref() {
            continue;
        }
        let kind = event
            .get("event")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let finished = client_event_finishes(command, kind);
        if finished {
            return Ok(());
        }
    }
    Err(io::Error::new(
        io::ErrorKind::UnexpectedEof,
        "daemon disconnected before completing the request",
    ))
}

fn client_event_finishes(command: &str, event: &str) -> bool {
    if event == "error" {
        return true;
    }
    match command {
        "status" => event == "status",
        "ping" => event == "pong",
        "control.snapshot" => event == "control.snapshot",
        "sharing.snapshot" => event == "sharing.snapshot",
        "sharing.preflight" => event == "sharing.preflight",
        "sharing.enable" | "sharing.disable" => event == "sharing.changed",
        "agent-host.status" => event == "agent-host.status",
        "config.apply" => event == "config.applied",
        "runtime.prepare" => event == "done",
        // The reset broadcasts `local.data-reset` on the way past; the run is
        // over when the stack has come back up, not when the wipe finished.
        "local.reset-data" => event == "done",
        _ => event == "done",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_shot_client_commands_stop_at_their_terminal_event() {
        assert!(client_event_finishes("status", "status"));
        assert!(client_event_finishes("ping", "pong"));
        assert!(client_event_finishes(
            "control.snapshot",
            "control.snapshot"
        ));
        assert!(client_event_finishes("config.apply", "config.applied"));
        assert!(client_event_finishes(
            "sharing.snapshot",
            "sharing.snapshot"
        ));
        assert!(client_event_finishes(
            "sharing.preflight",
            "sharing.preflight"
        ));
        assert!(client_event_finishes("sharing.enable", "sharing.changed"));
        assert!(client_event_finishes("sharing.disable", "sharing.changed"));
        assert!(client_event_finishes("runtime.prepare", "done"));
        assert!(!client_event_finishes(
            "runtime.prepare",
            "runtime.prepared"
        ));
        assert!(client_event_finishes("start", "done"));
        assert!(client_event_finishes("anything", "error"));
        assert!(!client_event_finishes("control.snapshot", "state"));
    }
}
