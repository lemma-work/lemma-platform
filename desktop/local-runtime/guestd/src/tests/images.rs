//! Image warm-up, repair, and the marker that says a cache is ready.

use super::*;

#[test]
fn sandbox_image_marker_probe_is_offline_and_checks_the_runtime_entrypoint() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![output(true, "")]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    assert!(service.sandbox_image_marker_is_ready(
        "ghcr.io/lemma/workspace@sha256:abc",
        WorkloadKind::Workspace,
    ));
    assert_eq!(
        service.engine.commands.lock().unwrap()[0],
        vec![
            "run",
            "--rm",
            "--network",
            "none",
            "--platform",
            guest_platform(),
            "ghcr.io/lemma/workspace@sha256:abc",
            "/bin/sh",
            "-c",
            "test -s \"$1\" || exit 3",
            "lemma-image-check",
            "/usr/local/bin/start-workspace-runtime",
        ]
    );
}

fn image_service(outputs: Vec<Output>) -> (tempfile::TempDir, GuestService<FakeEngine>) {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(outputs),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();
    (root, service)
}

const IMAGE: &str = "ghcr.io/lemma/runtime@sha256:abc";

/// An engine that could not run the check says nothing about the image, and
/// the image -- which running sandboxes may be made from -- is left alone.
#[test]
fn a_check_the_engine_could_not_run_removes_nothing() {
    let failed = Output {
        status: std::process::ExitStatus::from_raw(1 << 8),
        stdout: vec![],
        stderr: b"level=fatal msg=\"failed to create shim task: OCI runtime create failed: cannot allocate memory\"".to_vec(),
    };
    let (_root, service) = image_service(vec![output(true, "{}"), failed]);
    let error = service
        .ensure_sandbox_image(IMAGE, WorkloadKind::Workspace, true)
        .unwrap_err();
    assert_eq!(error.code, "image_check_failed");
    assert!(error.retryable);
    let commands = service.engine.commands();
    assert_eq!(commands.len(), 2, "{commands:?}");
    assert!(!commands.iter().any(|command| command[0] == "rmi"));
}

/// Incomplete, but a running sandbox is made from it: kept. And incomplete
/// with its registry unreachable: kept, since it could not be fetched back.
#[test]
fn an_incomplete_image_is_kept_while_in_use_or_offline() {
    let (_root, service) = image_service(vec![
        output(true, "{}"),
        exited(3),
        output(true, "0123456789abcdef\n"),
    ]);
    let error = service
        .ensure_sandbox_image(IMAGE, WorkloadKind::Workspace, true)
        .unwrap_err();
    assert_eq!(error.code, "image_check_failed");
    assert!(
        error.message.contains("running sandboxes"),
        "{}",
        error.message
    );
    assert!(!service.engine.commands().iter().any(|c| c[0] == "rmi"));

    let (_root, mut service) = image_service(vec![output(true, "{}"), exited(3), output(true, "")]);
    service.registry_reachable = |_| false;
    let error = service
        .ensure_sandbox_image(IMAGE, WorkloadKind::Workspace, true)
        .unwrap_err();
    assert!(
        error.message.contains("cannot be reached"),
        "{}",
        error.message
    );
    assert!(!service.engine.commands().iter().any(|c| c[0] == "rmi"));
}

/// Checked once per image per boot: a second sandbox from the same image
/// starts no check container, and a new boot checks again.
#[test]
fn a_passed_check_is_remembered_until_the_guest_boots_again() {
    let (root, mut service) = image_service(vec![
        output(true, "{}"),
        output(true, ""),
        output(true, "{}"),
        output(true, "{}"),
        output(true, ""),
    ]);
    service.boot_id = Some("boot-1".into());
    service
        .ensure_sandbox_image(IMAGE, WorkloadKind::Workspace, true)
        .unwrap();
    service
        .ensure_sandbox_image(IMAGE, WorkloadKind::Workspace, true)
        .unwrap();
    let runs = |service: &GuestService<FakeEngine>| {
        service
            .engine
            .commands()
            .iter()
            .filter(|command| command[0] == "run")
            .count()
    };
    assert_eq!(runs(&service), 1);

    service.boot_id = Some("boot-2".into());
    service
        .ensure_sandbox_image(IMAGE, WorkloadKind::Workspace, true)
        .unwrap();
    assert_eq!(runs(&service), 2);
    drop(root);
}

#[test]
fn image_repair_pull_explicitly_unpacks_the_selected_platform() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![output(true, "")]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    service
        .pull_image("ghcr.io/lemma/runtime@sha256:abc")
        .unwrap();
    assert_eq!(
        service.engine.commands.lock().unwrap()[0],
        vec![
            "pull",
            "--quiet",
            "--unpack=true",
            "--platform",
            guest_platform(),
            "ghcr.io/lemma/runtime@sha256:abc",
        ]
    );
}

#[test]
fn incomplete_sandbox_image_reference_is_replaced_before_repull() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![
            output(true, "{}"),
            exited(3),
            output(true, ""),
            output(true, ""),
            output(true, ""),
            output(true, ""),
            output(true, ""),
        ]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    service
        .ensure_sandbox_image(
            "ghcr.io/lemma/runtime@sha256:abc",
            WorkloadKind::Workspace,
            true,
        )
        .unwrap();
    let commands = service.engine.commands.lock().unwrap();
    assert_eq!(
        commands[2],
        vec![
            "ps",
            "--quiet",
            "--filter",
            "ancestor=ghcr.io/lemma/runtime@sha256:abc"
        ]
    );
    assert_eq!(commands[3], vec!["container", "prune", "--force"]);
    assert_eq!(
        commands[4],
        vec!["rmi", "--force", "ghcr.io/lemma/runtime@sha256:abc"]
    );
    assert_eq!(commands[5][0], "pull");
}

#[test]
fn unrecoverable_image_cache_persists_a_health_gated_reset_marker() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![
            output(true, "{}"),
            exited(3),
            output(true, ""),
            output(true, ""),
            output(true, ""),
            output(true, ""),
            exited(3),
            output(true, ""),
        ]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    let error = service
        .ensure_sandbox_image(
            "ghcr.io/lemma/runtime@sha256:abc",
            WorkloadKind::Workspace,
            true,
        )
        .unwrap_err();

    assert_eq!(error.code, "guest_cache_repair_required");
    let marker = service.cache_reset_marker();
    assert!(marker.is_file());
    assert_eq!(service.health().unwrap()["status"], "ready");
    let repair_due = marker.metadata().unwrap().modified().unwrap()
        + CACHE_REPAIR_RESPONSE_GRACE
        + Duration::from_secs(1);
    assert_eq!(
        service.health_at(repair_due).unwrap_err().code,
        "guest_cache_repair_required"
    );
}

/// A download already running is joined, not started again.
///
/// The table that used to answer this lived in process memory, which is the
/// wrong place on Windows: `wsl.exe --exec lemma-guestd request` gives every
/// request its own process, so the table was empty each time and each
/// `sandbox.ensure` began another transfer of the gigabyte the previous one
/// was still fetching.
///
/// The claim is a record lock, which excludes other processes, behind a table
/// of the claims this process holds, which excludes its other threads -- so
/// holding one here is the same obstacle a second guestd meets.
#[test]
fn a_download_already_under_way_is_joined_rather_than_started_again() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![output(true, "")]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();
    let image = "ghcr.io/lemma/workspace@sha256:abc";

    let held = claim_pull(&service.pull_claims(), image)
        .unwrap()
        .expect("the first claim is free");

    let error = service.pull_image(image).expect_err("somebody else has it");
    assert_eq!(error.code, "image_pulling");
    assert!(error.retryable);
    assert!(
        service.engine.commands.lock().unwrap().is_empty(),
        "a second download must not be started beside the first"
    );

    drop(held);
    // Immediately, with nothing in between. This once failed about one run in
    // eight at `--test-threads=8`, blamed on the kernel being slow to publish
    // a released `flock`. It was a sibling test's `fork` copying the claim's
    // descriptor into a child that had not reached `exec` yet; see
    // `a_child_forked_during_a_download_does_not_keep_its_claim`.
    service.pull_image(image).expect("the claim was released");
    assert_eq!(service.engine.commands.lock().unwrap()[0][0], "pull");
}

/// Two images are two claims, not one queue.
#[test]
fn one_download_does_not_hold_up_a_different_image() {
    let root = tempdir().unwrap();
    let claims = root.path().join("pulls");
    let _held = claim_pull(&claims, "ghcr.io/lemma/workspace@sha256:abc")
        .unwrap()
        .expect("the first claim is free");
    assert!(
        claim_pull(&claims, "ghcr.io/lemma/function@sha256:abc")
            .unwrap()
            .is_some(),
        "a different image is a different download"
    );
    assert_ne!(
        claim_name("ghcr.io/lemma/workspace@sha256:abc"),
        claim_name("ghcr.io/lemma/function@sha256:abc"),
    );
}

/// An engine that does what the rest of the guest does at any moment: fork.
///
/// Every pull forks a child that is still alive when the pull returns, the
/// way a `nerdctl`, `ctr` or `curl` started by another thread of guestd can
/// be between its `fork` and its `exec` when a download finishes. The child
/// has a copy of every descriptor the process had open, the pull's claim
/// among them.
struct ForksDuringPull {
    pulls: std::sync::atomic::AtomicUsize,
    children: Mutex<Vec<libc::pid_t>>,
}

impl Engine for ForksDuringPull {
    fn run(&self, arguments: &[String]) -> Result<Output, String> {
        assert_eq!(arguments[0], "pull");
        self.pulls.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        // SAFETY: the child calls only async-signal-safe functions and never
        // returns into Rust.
        let child = unsafe { libc::fork() };
        if child == 0 {
            unsafe {
                libc::sleep(30);
                libc::_exit(0);
            }
        }
        assert!(child > 0, "fork failed");
        self.children.lock().unwrap().push(child);
        Ok(output(false, ""))
    }
}

impl Drop for ForksDuringPull {
    fn drop(&mut self) {
        for child in self.children.lock().unwrap().drain(..) {
            end_process(child);
        }
    }
}

/// A failed download is retried by starting it again, not by being told it
/// is still running.
///
/// The interleaving that failed
/// `image_repair_and_failed_download_retries_leave_health_responsive` on CI:
/// a pull fails, a fork elsewhere in the process happened while it held its
/// claim, and the retry finds the claim still held -- by the child, which has
/// the claim's descriptor until it reaches `exec`. With a `flock` the retry
/// reported `Busy` and started nothing; the warm-up answered "still
/// downloading" for a download nobody was doing. Here the child lives for the
/// whole test, so this fails every time the claim can be inherited, rather
/// than once in a few hundred runs.
#[test]
fn a_child_forked_during_a_download_does_not_keep_its_claim() {
    let root = tempdir().unwrap();
    let claims = root.path().join("pulls");
    let image = "ghcr.io/lemma/workspace@sha256:forked";
    let engine = ForksDuringPull {
        pulls: std::sync::atomic::AtomicUsize::new(0),
        children: Mutex::new(Vec::new()),
    };

    assert!(matches!(
        pull_with(&engine, &claims, image),
        Err(PullFailure::Failed(_))
    ));
    match pull_with(&engine, &claims, image) {
        Err(PullFailure::Failed(_)) => {}
        Err(PullFailure::Busy) => panic!(
            "the retry was told the image is still downloading, but the only \
             holder of its claim is a child forked during the failed pull"
        ),
        Ok(()) => panic!("the engine fails every pull"),
    }
    assert_eq!(
        engine.pulls.load(std::sync::atomic::Ordering::SeqCst),
        2,
        "the retry must start a download of its own"
    );
    assert!(
        claim_pull(&claims, image).unwrap().is_some(),
        "nobody is downloading, so the claim is free"
    );
}

/// Take the claim file's record lock from a separate process, and keep it
/// until the returned process is ended. The flag is whether it got the lock.
fn lock_from_another_process(path: &std::path::Path) -> (libc::pid_t, bool) {
    use std::os::unix::ffi::OsStrExt;
    let path = std::ffi::CString::new(path.as_os_str().as_bytes()).unwrap();
    // SAFETY: all-zero is a valid `flock`; the fields that matter are set.
    let mut lock: libc::flock = unsafe { std::mem::zeroed() };
    lock.l_type = libc::F_WRLCK as libc::c_short;
    lock.l_whence = libc::SEEK_SET as libc::c_short;
    let mut pipe = [0; 2];
    // SAFETY: `pipe` has room for the two descriptors.
    assert_eq!(unsafe { libc::pipe(pipe.as_mut_ptr()) }, 0);
    // SAFETY: everything the child needs is allocated before the fork, and it
    // calls only async-signal-safe functions and never returns into Rust.
    let child = unsafe { libc::fork() };
    if child == 0 {
        unsafe {
            let fd = libc::open(
                path.as_ptr(),
                libc::O_RDWR | libc::O_CREAT,
                0o600 as libc::c_uint,
            );
            let locked = u8::from(fd >= 0 && libc::fcntl(fd, libc::F_SETLK, &lock) == 0);
            libc::write(pipe[1], std::ptr::addr_of!(locked).cast(), 1);
            libc::sleep(30);
            libc::_exit(0);
        }
    }
    assert!(child > 0, "fork failed");
    let mut locked = 0u8;
    // SAFETY: both descriptors are this test's; `locked` is one byte.
    unsafe {
        libc::close(pipe[1]);
        assert_eq!(
            libc::read(pipe[0], std::ptr::addr_of_mut!(locked).cast(), 1),
            1
        );
        libc::close(pipe[0]);
    }
    (child, locked == 1)
}

fn end_process(child: libc::pid_t) {
    // SAFETY: `child` is a process this test forked and has not reaped.
    unsafe {
        libc::kill(child, libc::SIGKILL);
        libc::waitpid(child, std::ptr::null_mut(), 0);
    }
}

/// The claim still excludes other processes -- the reason it is on disk.
///
/// Both ways round, because the threads of one process are excluded by a
/// table rather than by the lock: a second guestd is refused while this one
/// downloads, even after another attempt here was refused too (a refused
/// attempt must not open the file, since any close drops a record lock), and
/// this one is refused while a second guestd downloads, until it ends.
#[test]
fn a_claim_excludes_another_process_both_ways() {
    let root = tempdir().unwrap();
    let claims = root.path().join("pulls");
    let image = "ghcr.io/lemma/workspace@sha256:elsewhere";
    let path = claims.join(claim_name(image));

    let held = claim_pull(&claims, image).unwrap().expect("free at first");
    assert!(claim_pull(&claims, image).unwrap().is_none());
    let (child, locked) = lock_from_another_process(&path);
    end_process(child);
    assert!(
        !locked,
        "another process took a claim this one holds, after a refused attempt"
    );
    drop(held);

    let (child, locked) = lock_from_another_process(&path);
    assert!(locked, "the released claim was free to another process");
    let refused = claim_pull(&claims, image).unwrap().is_none();
    end_process(child);
    assert!(refused, "this process took a claim another process holds");
    assert!(
        claim_pull(&claims, image).unwrap().is_some(),
        "a claim is free once the process holding it has ended"
    );
}

/// A guest whose process ends with its reply downloads before it replies.
///
/// The contrast is the whole test. Given the same missing image and the same
/// engine, a resident guest answers "still downloading" and keeps fetching on
/// a thread -- correct, because that thread outlives the request. A
/// per-request guest that answered the same way would be answering about a
/// download it was about to kill.
#[test]
fn a_per_request_guest_finishes_the_download_before_it_answers() {
    let image = "ghcr.io/lemma/workspace@sha256:abc";
    let (release, downloads) = std::sync::mpsc::channel();
    let (started, _observed) = std::sync::mpsc::channel();
    let resident = GuestService::new(
        GatedPullEngine::new(downloads, started),
        tempdir().unwrap().path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();
    release.send(true).unwrap();
    let deferred = resident
        .ensure_sandbox_image_for_start(image, WorkloadKind::Workspace)
        .expect_err("a resident guest defers the download to a worker");
    assert_eq!(deferred.code, "image_pulling");

    let root = tempdir().unwrap();
    let (release, downloads) = std::sync::mpsc::channel();
    let (started, _observed) = std::sync::mpsc::channel();
    let mut per_request = GuestService::new(
        GatedPullEngine::new(downloads, started),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();
    per_request.set_per_request_process();
    release.send(true).unwrap();
    per_request
        .ensure_sandbox_image_for_start(image, WorkloadKind::Workspace)
        .expect("the image is here by the time the caller is answered");
}

/// A download that fails is reported to whoever asked for it.
///
/// It used to be written into a table in a process that was already exiting,
/// so nothing ever read it: a workspace whose image could not be fetched at
/// all was told "still downloading", and told it again on every retry, for
/// ever.
#[test]
fn a_failed_download_reaches_the_caller_that_asked_for_it() {
    let root = tempdir().unwrap();
    let (release, downloads) = std::sync::mpsc::channel();
    let (started, _observed) = std::sync::mpsc::channel();
    let mut service = GuestService::new(
        GatedPullEngine::new(downloads, started),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();
    service.set_per_request_process();
    release.send(false).unwrap();

    let error = service
        .ensure_sandbox_image_for_start(
            "ghcr.io/lemma/workspace@sha256:abc",
            WorkloadKind::Workspace,
        )
        .expect_err("the registry refused");
    assert_ne!(
        error.code, "image_pulling",
        "a download that failed is not a download still running"
    );
    assert!(
        error.message.contains("registry unavailable"),
        "the caller is told what actually went wrong: {}",
        error.message
    );
}
