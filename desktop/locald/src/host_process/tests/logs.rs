//! Rotating a log out from under the process writing it.

use super::*;

/// A running service's log is truncated under the writer that holds it.
///
/// This is the property the whole rotation scheme depends on and the reason
/// it copies aside instead of renaming: the child owns this descriptor for
/// its entire life, so a rename would leave it writing into the rotated
/// file and the live one frozen at zero forever. Asserted with a handle
/// still open, because that is the only state that ever actually occurs.
#[test]
fn a_live_service_log_is_rotated_under_the_process_writing_it() {
    use std::io::Write;

    let root = tempdir().unwrap();
    let path = root.path().join("backend.log");
    let mut writer = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .unwrap();
    writer.write_all(&vec![b'x'; 1024]).unwrap();

    // Under the ceiling: left exactly alone.
    rotate_log(&path, 4096).unwrap();
    assert_eq!(path.metadata().unwrap().len(), 1024);
    assert!(!path.with_extension("previous.log").exists());

    // Over it: copied aside and truncated back to zero.
    writer.write_all(&vec![b'x'; 4096]).unwrap();
    rotate_log(&path, 4096).unwrap();
    assert_eq!(path.metadata().unwrap().len(), 0);
    assert_eq!(
        path.with_extension("previous.log")
            .metadata()
            .unwrap()
            .len(),
        5120
    );

    // And the writer that never let go keeps appending to the same file,
    // which is now counting up from zero rather than from 5 KiB.
    writer.write_all(b"after").unwrap();
    writer.flush().unwrap();
    assert_eq!(path.metadata().unwrap().len(), 5);
}
