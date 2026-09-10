//! The guard that keeps unix-only tests off the Windows job.

/// Every test that drives a real process is gated to the platforms that
/// can drive one.
///
/// The helpers below -- `long_running_command`, `crash`, `wait_for_running`,
/// `wait_for_recorded_exit` -- are `#[cfg(unix)]`, because they signal
/// process groups and shell out to `sh`. A test that uses one without the
/// same gate does not fail on Windows, it fails to *compile*, and the only
/// place that shows up is the Windows CI job -- which is not in the desktop
/// filter, so the feedback arrives a push or two later. It has now cost
/// three round trips.
///
/// A source lint rather than a convention, in the shape `lib.rs` already
/// uses for the console-window rule.
#[test]
fn a_test_that_drives_a_real_process_is_gated_to_unix() {
    const HELPERS: [&str; 4] = [
        "long_running_command(",
        "crash(&",
        "wait_for_running(&",
        "wait_for_recorded_exit(&",
    ];
    // A POSIX binary is the other way a test needs a unix host, and it is
    // the one that cost the third round: four stamp tests ran `/bin/sh` and
    // `/usr/bin/false`. Three failed on Windows with "The system cannot
    // find the path specified"; the fourth *passed*, because it asserts the
    // setup fails and a missing binary fails too -- proving nothing, in
    // green.
    const POSIX_BINARIES: [&str; 3] = ["\"/bin/", "\"/usr/bin/", "\"/sbin/"];
    const NAMED_NOT_RUN: [&str; 1] = ["a_checkout_never_reaches_for_the_keychain"];
    // Every source file in this crate, not just this one. Both rounds of
    // Windows failures were in here, but the next one need not be -- and a
    // lint that only reads its own file is a lint that moves the problem.
    // Read from disk rather than listed: this file used to name sixteen, and
    // two of those sixteen have since become directories.
    let sources = crate::locald_sources();

    let mut ungated = Vec::new();
    for (file, source) in &sources {
        let lines: Vec<&str> = source.lines().collect();
        for (index, line) in lines.iter().enumerate() {
            let Some(name) = line.trim().strip_prefix("fn ") else {
                continue;
            };
            // A test function: `#[test]` on one of the few lines above it.
            let preamble = &lines[index.saturating_sub(4)..index];
            if !preamble.iter().any(|line| line.trim() == "#[test]") {
                continue;
            }
            let gated = preamble.iter().any(|line| line.trim() == "#[cfg(unix)]");
            if gated {
                continue;
            }
            // The body, to its closing brace at the function's own
            // indentation. That used to be spelled `"    }"`, because every
            // test in this crate lived inside a `mod tests {`. They are
            // files now, indented at nothing -- and a terminator that never
            // matches does not fail, it swallows the rest of the file, so
            // every ungated test inherited the POSIX binaries of the ones
            // below it.
            let indent = line.len() - line.trim_start().len();
            let closing = format!("{}}}", " ".repeat(indent));
            let body: String = lines[index..]
                .iter()
                .take_while(|line| **line != closing)
                .copied()
                .collect::<Vec<_>>()
                .join("\n");
            let name = name.split('(').next().unwrap_or(name);
            // This test names the helpers in order to look for them.
            if name == "a_test_that_drives_a_real_process_is_gated_to_unix" {
                continue;
            }
            // A POSIX path that is named and never run. `source_bindings_with`
            // is *told* where `uv` and `node` would be and asked where secrets
            // go, which is not a question about this host. Exempt by name
            // rather than gated: gating it would stop it running on Windows,
            // where the keychain question it answers is just as real. Found by
            // this guard once it started reading the whole crate instead of a
            // list of sixteen files.
            if NAMED_NOT_RUN.contains(&name) {
                continue;
            }
            let needs_unix = HELPERS.iter().any(|helper| body.contains(helper))
                || POSIX_BINARIES.iter().any(|path| body.contains(path));
            if needs_unix {
                ungated.push(format!("{file}::{name}"));
            }
        }
    }

    assert!(
        ungated.is_empty(),
        "these tests need a unix host -- a helper that signals a process \
         group, or a POSIX binary to run -- and are not #[cfg(unix)]. On \
         Windows they either fail to compile or fail to find the binary: \
         {ungated:?}",
    );
}
