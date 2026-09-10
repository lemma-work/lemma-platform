//! Native credential APIs can wait indefinitely for OS consent. Run them in an
//! owned copy of locald so a deadline also ends the call, including mutations.
//! The executable retains locald's signed identity; secrets travel only in pipes.
use std::io::{self, Read, Write};
use std::process::Command;
use std::time::Duration;

use lemma_desktop_process::{run_with_input, SetupProcessError};
use serde::{Deserialize, Serialize};

use crate::operator_config::{PlatformVault, SecretVault};
use crate::NoConsoleWindow;

const MESSAGE_LIMIT: usize = 256 * 1024;
const BUDGET: Duration = Duration::from_secs(15);

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Request {
    version: u8,
    install_id: String,
    name: String,
    operation: Operation,
}

#[derive(Deserialize, Serialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum Operation {
    Get,
    Set { value: String },
    Delete,
}

#[derive(Deserialize, Serialize)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
enum Response {
    Value { value: Option<String> },
    Success,
    Unavailable,
}

pub(crate) fn invoke(
    install_id: &str,
    name: &str,
    operation: Operation,
) -> io::Result<Option<String>> {
    let mut command = Command::new(std::env::current_exe()?);
    command.no_console_window().arg("credential-vault");
    exchange(command, install_id, name, operation, BUDGET)
}

fn exchange(
    command: Command,
    install_id: &str,
    name: &str,
    operation: Operation,
    budget: Duration,
) -> io::Result<Option<String>> {
    if budget.is_zero() {
        return Err(io::Error::new(
            io::ErrorKind::TimedOut,
            "Credential deadline expired",
        ));
    }
    let is_read = matches!(operation, Operation::Get);
    let request = Request {
        version: 1,
        install_id: install_id.into(),
        name: name.into(),
        operation,
    };
    validate(&request)?;
    let input = serde_json::to_vec(&request).map_err(|_| invalid_message())?;
    if input.len() > MESSAGE_LIMIT {
        return Err(invalid_message());
    }
    let output = run_with_input(command, input, budget, MESSAGE_LIMIT).map_err(|error| {
        match error {
            SetupProcessError::TimedOut => io::Error::new(
                io::ErrorKind::TimedOut,
                if is_read {
                    "The operating-system credential store has not answered. Unlock it and retry. Your saved credentials and local data are preserved."
                } else {
                    "The operating-system credential change could not be confirmed before its deadline. Unlock the credential store, reopen settings and retry the change. Do not reset application data."
                },
            ),
            _ => unavailable(),
        }
    })?;
    if !output.status.success() {
        return Err(unavailable());
    }
    // Do not attach raw child output to errors: it can contain a credential.
    match serde_json::from_slice(&output.stdout).map_err(|_| unavailable())? {
        Response::Value { value } if is_read => Ok(value),
        Response::Success if !is_read => Ok(None),
        _ => Err(unavailable()),
    }
}

fn validate(request: &Request) -> io::Result<()> {
    if request.version != 1
        || request.install_id.is_empty()
        || request.install_id.len() > 256
        || request.install_id.contains([':', '\0'])
        || !(crate::operator_config::SECRET_NAMES.contains(&request.name.as_str())
            || request.name == "secret.encryption_keyset"
            || request.name == crate::credential_vault::MASTER_KEY_NAME)
    {
        return Err(invalid_message());
    }
    Ok(())
}

fn invalid_message() -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, "Invalid credential request")
}

fn unavailable() -> io::Error {
    io::Error::other("The operating-system credential store is unavailable. Unlock it and retry. Do not reset application data.")
}

/// Internal single-request CLI entry point. Never starts the daemon or exposes
/// an inbound server, and deliberately never logs a request or native error.
pub fn serve() -> io::Result<()> {
    // A forced parent exit cannot run Rust Drop on Unix. The helper must also
    // bound its own lifetime so an OS consent call cannot outlive its owner.
    #[cfg(unix)]
    let owner = unsafe { libc::getppid() };
    std::thread::Builder::new()
        .name("credential-deadline".into())
        .spawn(move || {
            let deadline = std::time::Instant::now() + BUDGET;
            loop {
                #[cfg(unix)]
                let owner_gone = unsafe { libc::getppid() } != owner;
                #[cfg(not(unix))]
                let owner_gone = false;
                if owner_gone || std::time::Instant::now() >= deadline {
                    std::process::exit(124);
                }
                std::thread::sleep(Duration::from_millis(100));
            }
        })?;
    serve_with(io::stdin().lock(), io::stdout().lock(), &NativeVault)
}

fn serve_with(input: impl Read, mut output: impl Write, vault: &dyn SecretVault) -> io::Result<()> {
    let mut bytes = Vec::new();
    input
        .take(MESSAGE_LIMIT as u64 + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() > MESSAGE_LIMIT {
        return Err(invalid_message());
    }
    let request: Request = serde_json::from_slice(&bytes).map_err(|_| invalid_message())?;
    validate(&request)?;
    let result = match request.operation {
        Operation::Get => vault
            .get(&request.install_id, &request.name)
            .map(|value| Response::Value { value }),
        Operation::Set { value } => vault
            .set(&request.install_id, &request.name, &value)
            .map(|()| Response::Success),
        Operation::Delete => vault
            .delete(&request.install_id, &request.name)
            .map(|()| Response::Success),
    };
    let response =
        serde_json::to_vec(&result.unwrap_or(Response::Unavailable)).map_err(|_| unavailable())?;
    if response.len() > MESSAGE_LIMIT {
        return Err(unavailable());
    }
    output.write_all(&response)
}

struct NativeVault;

impl SecretVault for NativeVault {
    fn get(&self, install_id: &str, name: &str) -> io::Result<Option<String>> {
        match PlatformVault::entry(install_id, name)?.get_password() {
            Ok(value) => Ok(Some(value)),
            Err(keyring::v1::Error::NoEntry) => Ok(None),
            Err(_) => Err(unavailable()),
        }
    }

    fn set(&self, install_id: &str, name: &str, value: &str) -> io::Result<()> {
        PlatformVault::entry(install_id, name)?
            .set_password(value)
            .map_err(|_| unavailable())
    }

    fn delete(&self, install_id: &str, name: &str) -> io::Result<()> {
        match PlatformVault::entry(install_id, name)?.delete_credential() {
            Ok(()) | Err(keyring::v1::Error::NoEntry) => Ok(()),
            Err(_) => Err(unavailable()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;
    use std::sync::Mutex;

    #[derive(Default)]
    struct MemoryVault(Mutex<BTreeMap<(String, String), String>>);

    impl SecretVault for MemoryVault {
        fn get(&self, install: &str, name: &str) -> io::Result<Option<String>> {
            Ok(self
                .0
                .lock()
                .unwrap()
                .get(&(install.into(), name.into()))
                .cloned())
        }
        fn set(&self, install: &str, name: &str, value: &str) -> io::Result<()> {
            self.0
                .lock()
                .unwrap()
                .insert((install.into(), name.into()), value.into());
            Ok(())
        }
        fn delete(&self, install: &str, name: &str) -> io::Result<()> {
            self.0
                .lock()
                .unwrap()
                .remove(&(install.into(), name.into()));
            Ok(())
        }
    }

    fn call(vault: &dyn SecretVault, install: &str, operation: Operation) -> Response {
        let input = serde_json::to_vec(&Request {
            version: 1,
            install_id: install.into(),
            name: "ai.api_key".into(),
            operation,
        })
        .unwrap();
        let mut output = Vec::new();
        serve_with(input.as_slice(), &mut output, vault).unwrap();
        serde_json::from_slice(&output).unwrap()
    }

    #[test]
    fn credential_protocol_preserves_absence_replacement_removal_and_installation_scope() {
        let vault = MemoryVault::default();
        assert!(matches!(
            call(&vault, "one", Operation::Get),
            Response::Value { value: None }
        ));
        for value in ["first", "replacement café"] {
            assert!(matches!(
                call(
                    &vault,
                    "one",
                    Operation::Set {
                        value: value.into()
                    }
                ),
                Response::Success
            ));
            assert!(
                matches!(call(&vault, "one", Operation::Get), Response::Value { value: Some(actual) } if actual == value)
            );
            assert!(matches!(
                call(&vault, "two", Operation::Get),
                Response::Value { value: None }
            ));
        }
        assert!(matches!(
            call(&vault, "one", Operation::Delete),
            Response::Success
        ));
        assert!(matches!(
            call(&vault, "one", Operation::Get),
            Response::Value { value: None }
        ));
        assert!(matches!(
            call(&vault, "one", Operation::Delete),
            Response::Success
        ));
    }

    #[test]
    fn invalid_or_oversized_requests_cannot_reach_the_native_store() {
        let vault = MemoryVault::default();
        for input in [
            br#"{"version":2,"install_id":"one","name":"ai.api_key","operation":{"action":"get"}}"#.to_vec(),
            br#"{"version":1,"install_id":"one:other","name":"ai.api_key","operation":{"action":"get"}}"#.to_vec(),
            br#"{"version":1,"install_id":"one","name":"unrelated","operation":{"action":"set","value":"private"}}"#.to_vec(),
            br#"{"version":1,"install_id":"one","name":"ai.api_key","operation":{"action":"set","value":"private","unexpected":true}}"#.to_vec(),
            vec![b' '; MESSAGE_LIMIT + 1],
        ] {
            let mut output = Vec::new();
            let error = serve_with(input.as_slice(), &mut output, &vault).unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::InvalidInput);
            assert!(!error.to_string().contains("private"));
            assert!(output.is_empty());
            assert!(vault.0.lock().unwrap().is_empty());
        }
    }

    #[test]
    fn native_failures_are_not_reported_as_missing_credentials_or_echoed() {
        struct Denied;
        impl SecretVault for Denied {
            fn get(&self, _: &str, _: &str) -> io::Result<Option<String>> {
                Err(io::Error::other("sensitive native diagnostic"))
            }
            fn set(&self, _: &str, _: &str, _: &str) -> io::Result<()> {
                Err(io::Error::from(io::ErrorKind::PermissionDenied))
            }
            fn delete(&self, _: &str, _: &str) -> io::Result<()> {
                Err(io::Error::from(io::ErrorKind::PermissionDenied))
            }
        }
        for operation in [
            Operation::Get,
            Operation::Set {
                value: "test".into(),
            },
            Operation::Delete,
        ] {
            let response = call(&Denied, "one", operation);
            assert!(matches!(response, Response::Unavailable));
            assert_eq!(
                serde_json::to_string(&response).unwrap(),
                r#"{"status":"unavailable"}"#
            );
        }
    }

    #[test]
    fn expired_budget_never_starts_a_native_process() {
        let mut command = Command::new("nonexistent-credential-helper");
        command.no_console_window();
        let error =
            exchange(command, "one", "ai.api_key", Operation::Get, Duration::ZERO).unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::TimedOut);
    }

    #[cfg(unix)]
    fn shell(script: &str, root: &std::path::Path) -> Command {
        let mut command = Command::new("/bin/sh");
        command.arg("-c").arg(script).current_dir(root);
        command
    }

    #[cfg(unix)]
    #[test]
    fn stalled_reads_and_mutations_end_the_owned_helper_and_allow_retry() {
        for operation in [
            Operation::Get,
            Operation::Set {
                value: "test".into(),
            },
            Operation::Delete,
        ] {
            let root = tempfile::tempdir().unwrap();
            let error = exchange(
                shell("echo $$ > pid; cat >/dev/null; exec sleep 60", root.path()),
                "one",
                "ai.api_key",
                operation,
                Duration::from_secs(1),
            )
            .unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::TimedOut);
            let pid: i32 = std::fs::read_to_string(root.path().join("pid"))
                .unwrap()
                .trim()
                .parse()
                .unwrap();
            // The supervisor must reap the actual process, not detach a native
            // call that could overwrite a later credential replacement.
            assert_eq!(unsafe { libc::kill(pid, 0) }, -1);
            assert_eq!(io::Error::last_os_error().raw_os_error(), Some(libc::ESRCH));
            let result = exchange(
                shell(
                    "cat >/dev/null; printf '%s' '{\"status\":\"value\",\"value\":\"current\"}'",
                    root.path(),
                ),
                "one",
                "ai.api_key",
                Operation::Get,
                Duration::from_secs(5),
            )
            .unwrap();
            assert_eq!(result.as_deref(), Some("current"));
        }
    }

    #[cfg(unix)]
    #[test]
    fn malformed_failed_or_wrong_kind_replies_never_echo_secret_output() {
        let root = tempfile::tempdir().unwrap();
        for script in [
            "cat >/dev/null; printf sensitive-value",
            "cat >/dev/null; printf sensitive-value >&2; exit 1",
            "cat >/dev/null; printf '%s' '{\"status\":\"success\"}'",
        ] {
            let error = exchange(
                shell(script, root.path()),
                "one",
                "ai.api_key",
                Operation::Get,
                Duration::from_secs(5),
            )
            .unwrap_err();
            assert!(!error.to_string().contains("sensitive-value"));
        }
    }
}
