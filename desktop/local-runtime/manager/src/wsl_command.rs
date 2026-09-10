//! Running `wsl.exe` under a budget, and reading what it prints.

#[cfg(any(windows, test))]
use super::*;

#[cfg(any(windows, test))]
pub(crate) fn registered_guest(
    success: bool,
    output: &[u8],
    distribution: &str,
) -> io::Result<bool> {
    if !success {
        return Err(io::Error::other("Windows could not list its local runtimes. The installation has been kept. Restart Windows and retry Recovery."));
    }
    Ok(decode_wsl_output(output)
        .lines()
        .any(|line| line.trim() == distribution))
}

#[cfg(any(windows, test))]
pub(crate) fn decode_wsl_output(value: &[u8]) -> String {
    let decoded = if value.len() >= 2 && value.iter().skip(1).step_by(2).any(|byte| *byte == 0) {
        // `as_chunks`, not `chunks_exact(2)`: clippy 1.98 rejects a constant
        // chunk size, and the typed pair drops the indexing this used to do.
        let (pairs, _odd_trailing_byte) = value.as_chunks::<2>();
        let words: Vec<u16> = pairs.iter().map(|pair| u16::from_le_bytes(*pair)).collect();
        String::from_utf16_lossy(&words).replace('\0', "")
    } else {
        String::from_utf8_lossy(value).into_owned()
    };
    // A UTF-16 BOM survives decoding as U+FEFF, and it is not whitespace, so
    // `trim()` leaves it on the first line -- enough to stop the first
    // distribution listed from ever matching its own name.
    decoded.replace('\u{feff}', "")
}

/// How long a `wsl.exe` invocation is given before it is killed.
///
/// Backstops against a hang, not performance targets, so deliberately
/// generous: a budget that is too tight fails a slow machine that would have
/// succeeded, and that is a worse trade than the wait it saves.
#[cfg(any(windows, test))]
pub(crate) fn wsl_budget(arguments: &[&str]) -> Duration {
    match arguments.first().copied().unwrap_or_default() {
        // Unpacks a several-hundred-megabyte rootfs onto a fresh ext4.vhdx.
        "--import" => Duration::from_secs(20 * 60),
        // Deletes that disk again.
        "--unregister" => Duration::from_secs(10 * 60),
        // Questions about state. These are what a wedged WSL service hangs,
        // and what the start path is waiting on when it does, so this budget
        // is what decides how long a broken installation stays silent.
        "--list" | "--status" | "--version" => Duration::from_secs(60),
        "--terminate" | "--shutdown" => Duration::from_secs(2 * 60),
        // `--distribution <name> --exec ...`: work inside the guest, up to and
        // including lemma-runtime-init bringing the whole stack up.
        _ => Duration::from_secs(15 * 60),
    }
}

/// Run wsl.exe under a time limit, and record what it said.
///
/// Every call used to end in `child.wait_with_output()` with nothing bounding
/// it. A wedged WSL service -- the most ordinary Windows failure there is, and
/// the state the machine is in for as long as a `wsl --shutdown` is in flight
/// -- makes even `--status` block forever. The start path then stopped dead:
/// no error, no log line, no way for the user to tell a hang from slow work,
/// and no way for the daemon to give up and say so.
///
/// Feeding stdin was unbounded in a second way. The bytes went out through a
/// blocking `write_all` *before* anything drained stdout, so a child that
/// filled its output pipe while this end was still filling its input pipe
/// deadlocked both halves. Today's only input is a 32-byte capability file, so
/// it fits; nothing said it had to keep fitting.
///
/// `lemma_desktop_process` answers both: it pumps stdin, stdout and stderr
/// concurrently, and on expiry kills the job object rather than leaving a
/// wsl.exe running that outlives the daemon which started it.
#[cfg(any(windows, test))]
pub(crate) fn run_wsl_command(
    executable: &Path,
    arguments: &[&str],
    input: Option<&[u8]>,
    budget: Duration,
    log_path: &Path,
) -> io::Result<std::process::Output> {
    rotate_log(log_path, 5 * 1024 * 1024)?;
    let mut command = Command::new(executable);
    command.no_console_window().args(arguments);
    let outcome = match input {
        Some(input) => lemma_desktop_process::run_with_input(
            command,
            input.to_vec(),
            budget,
            MAX_RESPONSE_BYTES,
        ),
        None => lemma_desktop_process::run(command, budget, MAX_RESPONSE_BYTES),
    };
    let mut log = private_appending_log(log_path)?;
    let output = match outcome {
        Ok(output) => output,
        Err(error) => {
            // Written before returning. The point of bounding these is that a
            // hang leaves evidence behind, and by the time the error reaches a
            // person it has usually been reshaped into something friendlier
            // that no longer names the command.
            writeln!(
                log,
                "lemma-runtime: wsl.exe {} -> {error}",
                arguments.join(" ")
            )?;
            return Err(wsl_run_failure(error, arguments, budget));
        }
    };
    writeln!(
        log,
        "lemma-runtime: wsl.exe {} -> {}",
        arguments.join(" "),
        output.status
    )?;
    // Both streams, whole, not the first line of one of them.
    //
    // `wsl_message` takes a single line because that is what an error message
    // needs; a log is a different job. The guest says why its data disk needs
    // repair by printing `lemma-data: needs-repair: <reason>` -- on stdout,
    // from `lemma-mount-data` and its siblings -- and on Windows this file is
    // the only place that could ever have carried it. It did not, so the host
    // waited out its whole two minutes and reported "did not become ready" for
    // a problem the guest had already named.
    //
    // Bounded by the rotation at the top of this function rather than by
    // truncation here, so a long boot keeps its detail and a busy one does not
    // grow without limit.
    for (stream, bytes) in [("stdout", &output.stdout), ("stderr", &output.stderr)] {
        if bytes.is_empty() {
            continue;
        }
        for line in decode_wsl_output(bytes).lines() {
            if !line.trim().is_empty() {
                writeln!(log, "  {stream}: {line}")?;
            }
        }
    }
    Ok(output)
}

/// Turn a supervision failure into the `io::Error` the callers expect.
///
/// The `Io` arm hands the original error back rather than restating it. Its
/// kind is load-bearing: `unregister_windows_guest` treats a `NotFound` from
/// spawning wsl.exe as "there is no WSL here to unregister from", and wrapping
/// it in `io::Error::other` would turn a clean uninstall into a failure.
#[cfg(any(windows, test))]
pub(crate) fn wsl_run_failure(
    error: lemma_desktop_process::SetupProcessError,
    arguments: &[&str],
    budget: Duration,
) -> io::Error {
    use lemma_desktop_process::SetupProcessError;
    match error {
        SetupProcessError::Io(error) => error,
        SetupProcessError::TimedOut => io::Error::new(
            io::ErrorKind::TimedOut,
            format!(
                "Windows did not answer `wsl {}` within {} seconds. WSL itself \
                 is usually stuck when this happens: run `wsl --shutdown` in a \
                 terminal, then start Lemma again.",
                arguments.join(" "),
                budget.as_secs()
            ),
        ),
        SetupProcessError::OutputLimit => io::Error::other(format!(
            "`wsl {}` produced more output than Lemma will read",
            arguments.join(" ")
        )),
        SetupProcessError::Cancelled => io::Error::new(
            io::ErrorKind::Interrupted,
            format!("`wsl {}` was cancelled", arguments.join(" ")),
        ),
    }
}

/// The first line of something wsl.exe said, in a form a person can read.
///
/// wsl.exe writes UTF-16LE. Decoding that as UTF-8 succeeds -- the NUL halves
/// are valid, they just become U+0000 -- so every WSL error reached the user as
/// text with a NUL between each letter. It also defeated the substring matching
/// that turns a message into an actionable error code, so no WSL failure could
/// ever be recognised as one.
#[cfg(any(windows, test))]
pub(crate) fn wsl_message(value: &[u8]) -> String {
    decode_wsl_output(value)
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .unwrap_or("managed runtime command failed")
        .to_owned()
}
