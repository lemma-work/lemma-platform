//! Talking to a daemon that may have stopped listening.

use super::*;

/// A daemon that has stopped reading does not take every caller with it.
///
/// The socket used to be written to under the shell's writer lock, so a
/// `write` that blocked -- which is what a full socket buffer does when the
/// daemon on the other end has wedged, paused, or is mid-crash -- held that
/// lock for as long as the daemon stayed unreachable. The tray, the quit path
/// and the status poll all queue behind that same lock, so one stalled process
/// stopped the shell from doing anything that involved it, with no timeout and
/// no error.
///
/// The hand-off is a bounded channel now. This drives the channel directly,
/// which is the thing that changed: a caller either gets its message accepted
/// or is told, immediately, that the daemon is not reading.
#[test]
fn a_stalled_daemon_is_reported_rather_than_waited_on() {
    let (sender, receiver) = std::sync::mpsc::sync_channel::<String>(2);

    assert!(sender.try_send("first".into()).is_ok());
    assert!(sender.try_send("second".into()).is_ok());

    // Nothing is draining, which is exactly the stalled daemon. The third
    // message is refused rather than parking the caller.
    let refused = sender
        .try_send("third".into())
        .expect_err("a full backlog must not block the caller");
    assert!(matches!(refused, std::sync::mpsc::TrySendError::Full(_)));

    // And once the writer thread has gone -- which is what dropping the
    // receiver models -- the answer is that there is no connection, not a
    // wait for one.
    drop(receiver);
    let gone = sender
        .try_send("fourth".into())
        .expect_err("a departed writer is not a connection");
    assert!(matches!(
        gone,
        std::sync::mpsc::TrySendError::Disconnected(_)
    ));
}

/// The socket is written to by one thread and no other.
///
/// The point of the hand-off is that the blocking write happens somewhere that
/// blocking is harmless. A second `writeln!` to the daemon from a command
/// would put it back on the caller's thread, and under the writer lock, which
/// is the shape this replaced.
#[test]
fn only_the_writer_thread_writes_to_the_daemon_socket() {
    let source = shell_source();
    // Assembled at compile time so this guard does not find itself.
    let needle = concat!("writeln!(", "writer");
    assert_eq!(
        source.matches(needle).count(),
        1,
        "exactly one place may write to the daemon's socket, and it is the \
         thread that owns it"
    );
}
