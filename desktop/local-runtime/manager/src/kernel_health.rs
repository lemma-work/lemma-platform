use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom};
use std::path::Path;

const CONSOLE_TAIL_BYTES: u64 = 512 * 1024;
pub(crate) const FAILURE: &str = "Lemma's Linux guest kernel crashed. Quit and reopen Lemma to restart the local runtime. Your stored data has not been reset. If this repeats, use Updates and recovery to repair the runtime and export diagnostics.";

/// The serial console remains readable even when guestd cannot answer.
pub(crate) fn check_console(path: &Path) -> io::Result<()> {
    let mut file = match File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
    };
    let length = file.metadata()?.len();
    file.seek(SeekFrom::Start(length.saturating_sub(CONSOLE_TAIL_BYTES)))?;
    let mut bytes = Vec::new();
    file.take(CONSOLE_TAIL_BYTES).read_to_end(&mut bytes)?;
    let text = String::from_utf8_lossy(&bytes);
    if text.lines().any(|line| {
        line.contains("Internal error: Oops")
            || line.contains("Kernel panic - not syncing:")
            || line.contains("Fixing recursive fault but reboot is needed!")
            || line.contains("BUG: Bad rss-counter state")
            || line.contains("BUG: Bad page state")
            || line.contains("Kernel stack overflow.")
    }) {
        return Err(io::Error::other(FAILURE));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn reports_kernel_faults_without_exposing_console_contents() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("console.log");
        check_console(&path).unwrap();
        for fault in [
            "[ 4.2] Internal error: Oops: 00000001 [#1] SMP",
            "[ 4.2] Internal error: Oops - Undefined instruction: 02000000 [#1] SMP",
            "[ 4.2] Kernel panic - not syncing: Fatal exception",
            "[ 4.2] Fixing recursive fault but reboot is needed!",
            "[ 4.2] BUG: Bad rss-counter state mm:0000 type:MM_FILEPAGES val:165",
            "[ 4.2] BUG: Bad page state in process systemd",
            "[ 4.2] Kernel stack overflow.",
        ] {
            fs::write(&path, format!("sensitive diagnostic\n{fault}\r\n")).unwrap();
            assert_eq!(check_console(&path).unwrap_err().to_string(), FAILURE);
        }
        fs::write(&path, "[ 4.2] Started Lemma private local runtime.\n").unwrap();
        check_console(&path).unwrap();
    }

    #[test]
    fn inspects_the_tail_of_large_logs() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("console.log");
        let mut text = "x".repeat(CONSOLE_TAIL_BYTES as usize + 100);
        text.push_str("\nKernel panic - not syncing: Fatal exception\n");
        fs::write(&path, text).unwrap();
        assert!(check_console(&path).is_err());
    }
}
