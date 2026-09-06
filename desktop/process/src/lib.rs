//! Supervision for short-lived installation, recovery, and discovery commands.
//!
//! This owns a process tree for cleanup; it does not grant or enforce project
//! execution permissions and must not be used as an execution sandbox.

use std::io;
use std::process::{Command, Output, Stdio};
use std::time::Duration;

use process_wrap::tokio::{ChildWrapper, CommandWrap, KillOnDrop};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWriteExt};

#[derive(Debug, thiserror::Error)]
pub enum SetupProcessError {
    #[error("command exceeded its time limit")]
    TimedOut,
    #[error("command exceeded its output limit")]
    OutputLimit,
    #[error("command failed: {0}")]
    Io(#[from] io::Error),
}

struct OwnedProcess(Box<dyn ChildWrapper>);

impl Drop for OwnedProcess {
    fn drop(&mut self) {
        // Kill remaining descendants even when the command's leader exited.
        let _ = self.0.start_kill();
    }
}

pub fn run(
    command: Command,
    timeout: Duration,
    output_limit: usize,
) -> Result<Output, SetupProcessError> {
    run_with_optional_input(command, None, timeout, output_limit)
}

pub fn run_with_input(
    command: Command,
    input: Vec<u8>,
    timeout: Duration,
    output_limit: usize,
) -> Result<Output, SetupProcessError> {
    run_with_optional_input(command, Some(input), timeout, output_limit)
}

fn run_with_optional_input(
    command: Command,
    input: Option<Vec<u8>>,
    timeout: Duration,
    output_limit: usize,
) -> Result<Output, SetupProcessError> {
    // Discovery is synchronous and is also invoked by async host operations.
    // A dedicated runtime avoids nesting block_on inside their Tokio runtime.
    std::thread::Builder::new()
        .name("agent-setup".into())
        .spawn(move || {
            tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()?
                .block_on(run_async_with_input(command, input, timeout, output_limit))
        })?
        .join()
        .map_err(|_| io::Error::other("setup supervisor panicked"))?
}

#[cfg(test)]
async fn run_async(
    command: Command,
    timeout: Duration,
    output_limit: usize,
) -> Result<Output, SetupProcessError> {
    run_async_with_input(command, None, timeout, output_limit).await
}

async fn run_async_with_input(
    command: Command,
    input: Option<Vec<u8>>,
    timeout: Duration,
    output_limit: usize,
) -> Result<Output, SetupProcessError> {
    let mut command = tokio::process::Command::from(command);
    command
        .stdin(if input.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut command = CommandWrap::from(command);
    command.wrap(KillOnDrop);
    #[cfg(unix)]
    command.wrap(process_wrap::tokio::ProcessGroup::leader());
    #[cfg(windows)]
    {
        // JobObject starts suspended, assigns ownership, then resumes. Preserve
        // NO_WINDOW through the wrapper so it does not overwrite our flags.
        command.wrap(process_wrap::tokio::CreationFlags(
            windows::Win32::System::Threading::CREATE_NO_WINDOW,
        ));
        command.wrap(process_wrap::tokio::JobObject);
    }
    let mut child = OwnedProcess(command.spawn()?);
    let stdout = child
        .0
        .stdout()
        .take()
        .ok_or_else(|| io::Error::other("missing stdout"))?;
    let stderr = child
        .0
        .stderr()
        .take()
        .ok_or_else(|| io::Error::other("missing stderr"))?;
    let stdin = child.0.stdin().take();
    let result = tokio::time::timeout(timeout, async {
        let (status, stdout, stderr, input_result) = tokio::try_join!(
            async {
                // A Windows job wait includes descendants. Wait for the leader
                // first so a successful setup command cannot be held open by
                // a background child; its remaining children belong to us.
                let status = child.0.inner_mut().wait().await?;
                let _ = child.0.start_kill();
                Ok::<_, SetupProcessError>(status)
            },
            read_bounded(stdout, output_limit),
            read_bounded(stderr, output_limit),
            async {
                let result = async {
                    if let (Some(mut stdin), Some(input)) = (stdin, input) {
                        stdin.write_all(&input).await?;
                        stdin.shutdown().await?;
                    }
                    Ok::<_, io::Error>(())
                }
                .await;
                Ok::<_, SetupProcessError>(result)
            },
        )?;
        // A child may reject the request before reading stdin. Preserve its
        // status and diagnostic instead of replacing them with BrokenPipe.
        if status.success() {
            input_result?;
        }
        Ok(Output {
            status,
            stdout,
            stderr,
        })
    })
    .await
    .unwrap_or(Err(SetupProcessError::TimedOut));

    let _ = child.0.start_kill();
    // Await termination on error as well as success; dropping Tokio's child
    // requests a kill but does not synchronously reap it.
    child.0.wait().await?;
    result
}

async fn read_bounded(
    mut stream: impl AsyncRead + Unpin,
    limit: usize,
) -> Result<Vec<u8>, SetupProcessError> {
    let mut output = Vec::new();
    let mut buffer = vec![0; 8192];
    loop {
        let count = stream.read(&mut buffer).await?;
        if count == 0 {
            return Ok(output);
        }
        if count > limit.saturating_sub(output.len()) {
            return Err(SetupProcessError::OutputLimit);
        }
        output.extend_from_slice(&buffer[..count]);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn fixture_command(mode: &str) -> Command {
        let mut command = Command::new(std::env::current_exe().unwrap());
        command.args([
            "--exact",
            "tests::process_fixture",
            "--ignored",
            "--nocapture",
        ]);
        command.env("LEMMA_SETUP_TEST_MODE", mode);
        command
    }

    #[test]
    fn stdin_and_both_outputs_make_progress_together() {
        let input = vec![b'i'; 512 * 1024];
        let output = run_with_input(
            fixture_command("stdin-echo"),
            input.clone(),
            Duration::from_secs(5),
            2 * 1024 * 1024,
        )
        .unwrap();
        assert!(output.status.success());
        assert!(output.stdout.windows(input.len()).any(|part| part == input));
        assert_eq!(output.stderr, vec![b'e'; 256 * 1024]);
    }

    #[test]
    fn an_unread_stdin_pipe_is_covered_by_the_same_deadline() {
        assert!(matches!(
            run_with_input(
                fixture_command("stdin-stall"),
                vec![b'i'; 2 * 1024 * 1024],
                Duration::from_millis(100),
                4096
            ),
            Err(SetupProcessError::TimedOut)
        ));
    }

    #[test]
    fn an_early_rejection_keeps_the_childs_diagnostic() {
        let output = run_with_input(
            fixture_command("reject-input"),
            vec![b'i'; 2 * 1024 * 1024],
            Duration::from_secs(5),
            4096,
        )
        .unwrap();
        assert_eq!(output.status.code(), Some(23));
        assert_eq!(output.stderr, b"unsupported request");
    }

    #[test]
    #[ignore = "subprocess fixture invoked by the supervision tests"]
    fn process_fixture() {
        let Ok(mode) = std::env::var("LEMMA_SETUP_TEST_MODE") else {
            return;
        };
        match mode.as_str() {
            "output" => {
                std::io::stdout().write_all(b"setup stdout").unwrap();
                std::io::stderr().write_all(b"setup stderr").unwrap();
            }
            "stdin-echo" => {
                use std::io::Read;
                std::io::stdout()
                    .write_all(&vec![b'o'; 256 * 1024])
                    .unwrap();
                std::io::stderr()
                    .write_all(&vec![b'e'; 256 * 1024])
                    .unwrap();
                let mut input = Vec::new();
                std::io::stdin().read_to_end(&mut input).unwrap();
                std::io::stdout().write_all(&input).unwrap();
            }
            "stdin-stall" => std::thread::sleep(Duration::from_secs(10)),
            "reject-input" => {
                std::io::stderr().write_all(b"unsupported request").unwrap();
                std::process::exit(23);
            }
            "failure" => std::process::exit(23),
            "stdout-flood" | "stderr-flood" => {
                let mut stream: Box<dyn Write> = if mode == "stdout-flood" {
                    Box::new(std::io::stdout())
                } else {
                    Box::new(std::io::stderr())
                };
                for _ in 0..128 {
                    if stream.write_all(&[b'x'; 8192]).is_err() {
                        break;
                    }
                }
            }
            "leaf" => {
                let directory = std::env::var_os("LEMMA_SETUP_TEST_DIRECTORY").unwrap();
                let directory = std::path::PathBuf::from(directory);
                std::fs::write(directory.join("started"), b"ready").unwrap();
                std::thread::sleep(Duration::from_secs(3));
                std::fs::write(directory.join("finished"), b"unexpected survivor").unwrap();
            }
            "tree-timeout" | "tree-success" => {
                let directory = std::path::PathBuf::from(
                    std::env::var_os("LEMMA_SETUP_TEST_DIRECTORY").unwrap(),
                );
                let mut child = fixture_command("leaf")
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .spawn()
                    .unwrap();
                let deadline = std::time::Instant::now() + Duration::from_secs(2);
                while !directory.join("started").exists() && std::time::Instant::now() < deadline {
                    std::thread::sleep(Duration::from_millis(10));
                }
                if mode == "tree-success" {
                    // The supervisor must clean descendants even after their
                    // leader returns and closes its output streams.
                    std::process::exit(0);
                }
                // Finite fallback guarantees fixture cleanup even if the
                // supervisor regression under test stops killing descendants.
                child.wait().unwrap();
            }
            _ => panic!("unknown fixture"),
        }
    }

    #[test]
    fn captures_both_streams_and_exit_status() {
        let output = run(fixture_command("output"), Duration::from_secs(5), 4096).unwrap();
        assert!(output.status.success());
        assert!(String::from_utf8_lossy(&output.stdout).contains("setup stdout"));
        assert!(String::from_utf8_lossy(&output.stderr).contains("setup stderr"));
        let failed = run(fixture_command("failure"), Duration::from_secs(5), 4096).unwrap();
        assert_eq!(failed.status.code(), Some(23));
    }

    #[test]
    fn either_stream_exceeding_limit_stops_command() {
        for mode in ["stdout-flood", "stderr-flood"] {
            let result = run(fixture_command(mode), Duration::from_secs(5), 256 * 1024);
            assert!(
                matches!(result, Err(SetupProcessError::OutputLimit)),
                "{result:?}"
            );
        }
    }

    #[test]
    fn missing_command_returns_io_error() {
        let directory = tempfile::tempdir().unwrap();
        let result = run(
            Command::new(directory.path().join("missing")),
            Duration::from_secs(1),
            4096,
        );
        assert!(matches!(result, Err(SetupProcessError::Io(_))));
    }

    #[tokio::test]
    async fn synchronous_discovery_can_run_inside_an_async_caller() {
        assert!(
            run(fixture_command("output"), Duration::from_secs(5), 4096)
                .unwrap()
                .status
                .success()
        );
    }

    fn assert_tree_cleanup(mode: &str) {
        let directory = tempfile::Builder::new()
            .prefix("lemma setup ü ")
            .tempdir()
            .unwrap();
        let mut command = fixture_command(mode);
        command.env("LEMMA_SETUP_TEST_DIRECTORY", directory.path());
        let result = run(command, Duration::from_secs(2), 4096);
        // Wait beyond the fixture's bounded lifetime before any assertion, so
        // a failing regression cannot leave a child running after the test.
        std::thread::sleep(Duration::from_secs(4));
        assert!(
            directory.path().join("started").exists(),
            "fixture never became ready: {result:?}"
        );
        assert!(
            !directory.path().join("finished").exists(),
            "descendant survived {mode}"
        );
        if mode == "tree-timeout" {
            assert!(
                matches!(result, Err(SetupProcessError::TimedOut)),
                "{result:?}"
            );
        } else {
            assert!(result.unwrap().status.success());
        }
    }

    #[test]
    fn timeout_stops_descendants() {
        assert_tree_cleanup("tree-timeout");
    }

    #[test]
    fn successful_leader_does_not_leave_descendants() {
        assert_tree_cleanup("tree-success");
    }

    #[tokio::test]
    async fn dropping_supervision_stops_descendants() {
        let directory = tempfile::tempdir().unwrap();
        let mut command = fixture_command("tree-timeout");
        command.env("LEMMA_SETUP_TEST_DIRECTORY", directory.path());
        let task = tokio::spawn(run_async(command, Duration::from_secs(10), 4096));
        let deadline = tokio::time::Instant::now() + Duration::from_secs(2);
        while !directory.path().join("started").exists() && tokio::time::Instant::now() < deadline {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        task.abort();
        let result = task.await;
        tokio::time::sleep(Duration::from_secs(4)).await;
        assert!(
            directory.path().join("started").exists(),
            "fixture never became ready"
        );
        assert!(result.unwrap_err().is_cancelled());
        assert!(
            !directory.path().join("finished").exists(),
            "descendant survived dropped supervisor"
        );
    }

    #[tokio::test]
    async fn bounded_reader_accepts_exact_limit_and_rejects_next_byte() {
        assert_eq!(read_bounded(&b"abc"[..], 3).await.unwrap(), b"abc");
        assert!(matches!(
            read_bounded(&b"abcd"[..], 3).await,
            Err(SetupProcessError::OutputLimit)
        ));
        assert!(read_bounded(&b""[..], 0).await.unwrap().is_empty());
    }
}
