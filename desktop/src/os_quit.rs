//! Quits that macOS issues rather than the app: Dock → Quit, log out,
//! restart and shut down.
//!
//! tao answers `applicationWillTerminate:` and nothing before it, so an
//! OS-issued terminate never raised `ExitRequested`: the shell exited on the
//! spot and `lemma-locald` stayed up supervising the VM, Postgres and the
//! backend with no window, no tray icon and nothing in the Dock -- exactly the
//! headless stack `leave_nothing_running` exists to prevent. Only the app's own
//! ⌘Q item reached `request_quit`.
//!
//! This adds `applicationShouldTerminate:` to tao's delegate class at runtime.
//! It answers `NSTerminateLater`, runs the ordinary quit on a worker, and
//! replies once that quit has either finished (terminate) or been declined
//! (cancel). A logout, restart or shutdown is not asked about: the person has
//! already said what they want, and a modal in front of a logout is how an app
//! ends up "preventing logout".

use super::*;

/// What `applicationShouldTerminate:` answers, and what it sets in motion.
/// Pure, so the rule is tested everywhere; only macOS asks it.
#[cfg(any(target_os = "macos", test))]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum OsTerminate {
    /// The shutdown worker has already finished; let AppKit terminate.
    Now,
    /// A quit is already stopping the stack. Wait for it and reply then.
    AwaitRunningQuit,
    /// The session is ending: stop the stack without asking, then reply.
    StopWithoutAsking,
    /// Dock → Quit and the like: the same question ⌘Q asks.
    AskThenStop,
}

#[cfg(any(target_os = "macos", test))]
pub(crate) fn os_terminate_disposition(
    may_exit: bool,
    quit_confirmed: bool,
    session_ending: bool,
) -> OsTerminate {
    if may_exit {
        OsTerminate::Now
    } else if quit_confirmed {
        OsTerminate::AwaitRunningQuit
    } else if session_ending {
        OsTerminate::StopWithoutAsking
    } else {
        OsTerminate::AskThenStop
    }
}

/// `kAEQuitReason` values that mean the login session itself is ending.
///
/// `'logo'`/`'rlgo'` log out, `'rrst'`/`'rest'` restart, `'rsdn'`/`'shut'`
/// shut down. A Dock quit carries no reason at all.
#[cfg(any(target_os = "macos", test))]
pub(crate) fn quit_reason_ends_session(reason: u32) -> bool {
    const ENDING: [&[u8; 4]; 6] = [b"logo", b"rlgo", b"rrst", b"rest", b"rsdn", b"shut"];
    ENDING
        .iter()
        .any(|code| u32::from_be_bytes(**code) == reason)
}

#[cfg(target_os = "macos")]
mod macos {
    use super::*;
    use objc2::ffi;
    use objc2::runtime::{AnyClass, AnyObject, Bool, Imp, Sel};
    use objc2::{class, msg_send, sel};
    use std::sync::OnceLock;

    static APP: OnceLock<AppHandle> = OnceLock::new();
    /// Set while AppKit is waiting on `replyToApplicationShouldTerminate:`.
    static REPLY_OWED: AtomicBool = AtomicBool::new(false);

    const NS_TERMINATE_NOW: usize = 1;
    const NS_TERMINATE_LATER: usize = 2;
    const QUIT_REASON_KEYWORD: u32 = u32::from_be_bytes(*b"why?");

    pub(crate) fn install(app: &AppHandle) {
        if APP.set(app.clone()).is_err() {
            return;
        }
        // SAFETY: called on the main thread after tao installed its delegate.
        // The method is added to that delegate's own (tao-registered) class,
        // with the encoding AppKit declares: NSApplicationTerminateReply
        // (NSUInteger) from (id self, SEL _cmd, NSApplication *sender).
        unsafe {
            let application: *mut AnyObject = msg_send![class!(NSApplication), sharedApplication];
            let delegate: *mut AnyObject = msg_send![application, delegate];
            if delegate.is_null() {
                append_install_log("[quit] no application delegate; OS quits bypass the stop");
                return;
            }
            let class = ffi::object_getClass(delegate) as *mut AnyClass;
            let imp: Imp = std::mem::transmute::<
                unsafe extern "C-unwind" fn(*mut AnyObject, Sel, *mut AnyObject) -> usize,
                Imp,
            >(should_terminate);
            let added = ffi::class_addMethod(
                class,
                sel!(applicationShouldTerminate:),
                imp,
                c"Q@:@".as_ptr(),
            );
            if !added.as_bool() {
                append_install_log("[quit] applicationShouldTerminate: was already defined");
            }
            // Re-set so AppKit re-reads which optional methods the delegate
            // answers, in case it cached that when tao first set it.
            let () = msg_send![application, setDelegate: delegate];
        }
    }

    unsafe extern "C-unwind" fn should_terminate(
        _this: *mut AnyObject,
        _cmd: Sel,
        _sender: *mut AnyObject,
    ) -> usize {
        let Some(app) = APP.get() else {
            return NS_TERMINATE_NOW;
        };
        let shell: State<Shell> = app.state();
        let disposition = os_terminate_disposition(
            shell.shutdown.may_exit(),
            shell.quit_confirmed.load(Ordering::Acquire),
            session_ending(),
        );
        append_install_log(&format!("[quit] macOS asked to terminate: {disposition:?}"));
        if disposition == OsTerminate::Now {
            return NS_TERMINATE_NOW;
        }
        REPLY_OWED.store(true, Ordering::Release);
        match disposition {
            OsTerminate::StopWithoutAsking => {
                let handle = app.clone();
                std::thread::spawn(move || stop_then_quit(&handle));
            }
            OsTerminate::AskThenStop => request_quit(app),
            OsTerminate::Now | OsTerminate::AwaitRunningQuit => {}
        }
        NS_TERMINATE_LATER
    }

    /// Whether the terminate being handled is a logout, restart or shutdown.
    fn session_ending() -> bool {
        // SAFETY: plain Foundation getters on the main thread; every pointer
        // is checked for nil before it is messaged.
        unsafe {
            let manager: *mut AnyObject =
                msg_send![class!(NSAppleEventManager), sharedAppleEventManager];
            if manager.is_null() {
                return false;
            }
            let event: *mut AnyObject = msg_send![manager, currentAppleEvent];
            if event.is_null() {
                return false;
            }
            let reason: *mut AnyObject =
                msg_send![event, attributeDescriptorForKeyword: QUIT_REASON_KEYWORD];
            if reason.is_null() {
                return false;
            }
            let code: u32 = msg_send![reason, enumCodeValue];
            quit_reason_ends_session(code)
        }
    }

    pub(crate) fn answer(app: &AppHandle, terminate: bool) {
        if !REPLY_OWED.swap(false, Ordering::AcqRel) {
            return;
        }
        let _ = app.run_on_main_thread(move || {
            // SAFETY: on the main thread, answering the NSTerminateLater this
            // module returned and has not answered yet.
            unsafe {
                let application: *mut AnyObject =
                    msg_send![class!(NSApplication), sharedApplication];
                let () = msg_send![
                    application,
                    replyToApplicationShouldTerminate: Bool::new(terminate)
                ];
            }
        });
    }
}

/// Hook OS-issued terminates into the quit path. A no-op off macOS.
pub(crate) fn install_os_quit_handler(app: &AppHandle) {
    #[cfg(target_os = "macos")]
    macos::install(app);
    #[cfg(not(target_os = "macos"))]
    let _ = app;
}

/// Answer a terminate macOS is waiting on, if there is one.
///
/// `true` once the stack is down and the app is leaving; `false` when the
/// person declined, or the stop could not be started, so a logout the app was
/// holding is released rather than left hanging.
pub(crate) fn answer_os_quit(app: &AppHandle, terminate: bool) {
    #[cfg(target_os = "macos")]
    macos::answer(app, terminate);
    #[cfg(not(target_os = "macos"))]
    let _ = (app, terminate);
}
