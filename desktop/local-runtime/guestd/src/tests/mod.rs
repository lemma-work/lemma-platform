//! The guest's guards, grouped the way the code they cover is grouped.

mod clock;
mod concurrency;
mod core_data;
mod data_binding;
mod diagnostics;
mod engine;
mod images;
mod inspect;
mod limits;
mod network;
mod protocol;
mod run_contract;

use super::*;
use crate::capacity::*;
use crate::protocol::*;
use crate::service::*;

/// Every source file of the guest agent, concatenated.
///
/// `lib.rs` was 6,257 lines and the guards below scanned it by name. It is a
/// directory of modules now, and a guard still reading one file would go on
/// passing while covering a fraction of what it used to.
///
/// From disk rather than a list of `include_str!`s: a list somebody maintains
/// is how a module goes unscanned.
pub(super) fn guest_source() -> String {
    let directory = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut files: Vec<std::path::PathBuf> = std::fs::read_dir(&directory)
        .expect("the guest's source directory")
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.extension().is_some_and(|kind| kind == "rs"))
        // The guards themselves, which would otherwise let a scan find its
        // own needles.
        .filter(|path| path.file_name().is_some_and(|name| name != "tests.rs"))
        .collect();
    files.sort();
    assert!(
        files.len() > 10,
        "the guest is a directory of modules; reading {} file(s) means the \
         scan is looking at a fraction of it",
        files.len(),
    );
    files
        .iter()
        .map(|path| std::fs::read_to_string(path).expect("a guest module"))
        .collect::<Vec<_>>()
        .join("\n")
        .replace("\r\n", "\n")
}

/// `lib.rs` was the guest agent; it is a module list now.
///
/// Three guards went on reading it by name after the split, and a guard that
/// searches less of the tree than it used to does not fail -- it passes.
#[test]
pub(super) fn no_guard_looks_for_the_guest_inside_lib() {
    // Assembled at compile time so this guard does not find itself.
    let needle = concat!("include_str!(\"lib", ".rs\")");
    let guards = std::fs::read_dir(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("src")
            .join("tests"),
    )
    .expect("the guard directory")
    .filter_map(Result::ok)
    .map(|entry| std::fs::read_to_string(entry.path()).expect("a guard module"))
    .collect::<Vec<_>>()
    .join("\n");
    assert!(
        !guards.contains(needle),
        "a guard that scans the guest's source reads guest_source(), which \
         sees every module rather than whichever one lib.rs still holds",
    );
}

pub(super) struct GatedPullEngine {
    release: Mutex<std::sync::mpsc::Receiver<bool>>,
    started: std::sync::mpsc::Sender<String>,
    present: Mutex<std::collections::HashSet<String>>,
    invalid: Mutex<std::collections::HashSet<String>>,
}

impl GatedPullEngine {
    pub(super) fn new(
        release: std::sync::mpsc::Receiver<bool>,
        started: std::sync::mpsc::Sender<String>,
    ) -> Self {
        Self {
            release: Mutex::new(release),
            started,
            present: Mutex::new(std::collections::HashSet::new()),
            invalid: Mutex::new(std::collections::HashSet::new()),
        }
    }
}

impl Engine for GatedPullEngine {
    fn run(&self, arguments: &[String]) -> Result<Output, String> {
        match arguments[0].as_str() {
            "pull" => {
                let image = arguments.last().unwrap().clone();
                self.started.send(image.clone()).unwrap();
                let success = self
                    .release
                    .lock()
                    .unwrap()
                    .recv_timeout(Duration::from_secs(10))
                    .map_err(|e| e.to_string())?;
                if success {
                    self.invalid.lock().unwrap().remove(&image);
                    self.present.lock().unwrap().insert(image);
                    Ok(output(true, ""))
                } else {
                    Err("registry unavailable".into())
                }
            }
            "image" => Ok(output(
                self.present
                    .lock()
                    .unwrap()
                    .contains(arguments.last().unwrap()),
                "",
            )),
            "run" => Ok(output(
                !self.invalid.lock().unwrap().contains(&arguments[6]),
                "",
            )),
            "rmi" => {
                self.present
                    .lock()
                    .unwrap()
                    .remove(arguments.last().unwrap());
                Ok(output(true, ""))
            }
            "container" | "ps" => Ok(output(true, "")),
            _ => Err(format!("unexpected engine command {arguments:?}")),
        }
    }
}

pub(super) fn wait_for_image_warmup(service: &GuestService<GatedPullEngine>) {
    let deadline = Instant::now() + Duration::from_secs(10);
    while service
        .image_warmups
        .lock()
        .unwrap()
        .values()
        .any(|state| matches!(state, ImageWarmupState::Running))
    {
        assert!(
            Instant::now() < deadline,
            "image preparation did not finish"
        );
        thread::sleep(Duration::from_millis(1));
    }
}
use std::cell::Cell;
use std::os::unix::process::ExitStatusExt;
use std::sync::Mutex;
use tempfile::{tempdir, TempDir};

pub(super) struct FakeEngine {
    commands: Mutex<Vec<Vec<String>>>,
    outputs: Mutex<Vec<Output>>,
}

impl FakeEngine {
    fn new(outputs: Vec<Output>) -> Self {
        Self {
            commands: Mutex::new(Vec::new()),
            outputs: Mutex::new(outputs.into_iter().rev().collect()),
        }
    }
}

impl Engine for FakeEngine {
    fn run(&self, arguments: &[String]) -> Result<Output, String> {
        self.commands.lock().unwrap().push(arguments.to_vec());
        self.outputs
            .lock()
            .unwrap()
            .pop()
            .ok_or_else(|| "no fake output".into())
    }
}

/// A ceiling on a test that hangs, not part of what any test asserts.
///
/// These budgets were all one second, which reads as harmless — the work
/// they bound takes milliseconds. But they are *wall-clock* deadlines, and
/// the suite runs its tests in parallel on a shared CI runner, so a 250ms
/// retry sleep or a `printf` can miss a one-second bus. Four tests failed
/// at random against deadlines none of them were about; under contention
/// one of them was measured taking over seven seconds.
///
/// Nothing is slower for it. Each of these tests is driven by a fake that
/// answers immediately or a script that exits, so the budget is only ever
/// reached when something is already broken.
pub(super) const UNHURRIED_TEST_TIMEOUT_SECS: u64 = 30;

/// How long the stand-in engine sleeps when a test needs it not to finish.
///
/// The timeout test proves the kill landed by returning long before this
/// elapses, so its assertion is written against this rather than against a
/// second constant that could drift away from it.
pub(super) const FORKING_ENGINE_SLEEP_SECS: u64 = 30;

pub(super) fn output(success: bool, stdout: &str) -> Output {
    Output {
        status: std::process::ExitStatus::from_raw(if success { 0 } else { 1 }),
        stdout: stdout.as_bytes().to_vec(),
        stderr: if success {
            vec![]
        } else {
            b"not found".to_vec()
        },
    }
}

pub(super) fn inspect() -> String {
    json!([{
        "Id": "sha256:exact-generation",
        "State": {"Running": true, "Status": "running"},
        "Config": {"Labels": {
            "lemma.work/workload-kind": "workspace",
            "lemma.work/image-ref": "ghcr.io/lemma/workspace@sha256:abc",
            "lemma.work/metadata": "{\"managed-by\":\"lemma-workspace\"}"
        }},
        "NetworkSettings": {"Ports": {
            "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49152"}],
            "4848/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49153"}]
        }}
    }])
    .to_string()
}

pub(super) fn core_parameters(postgres_image: &str) -> CoreParameters {
    CoreParameters {
        images: CoreImages {
            postgres: postgres_image.to_owned(),
            redis: "docker.io/redis:7.4-alpine".into(),
            supertokens: "docker.io/supertokens/supertokens-postgresql:11.4.5".into(),
            workspace: None,
            function: None,
        },
        credentials: CoreCredentials {
            postgres_password: "a".repeat(64),
            redis_password: "b".repeat(64),
        },
    }
}

/// Returns the temporary root with the service: dropping it early would
/// take the guest state directory out from under the service.
pub(super) fn clock_test_service() -> (TempDir, GuestService<FakeEngine>) {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![output(true, "")]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();
    (root, service)
}
