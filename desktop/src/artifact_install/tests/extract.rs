//! What comes out of an archive, and what must not.

use super::*;

#[test]
fn extraction_rejects_an_incorrect_signed_expanded_size() {
    let root = tempfile::tempdir().unwrap();
    let archive = root.path().join("runtime.zip");
    write_zip(&archive, &[("runtime/file", b"payload", 0o644)]);

    let error = extract_archive(
        &archive,
        &root.path().join("expanded"),
        1,
        "extract",
        "test",
        "Extracting test runtime",
        ProgressSpan {
            completed_before: 0,
            total: MAX_EXTRACTED_BYTES as u64,
        },
        &mut |_| {},
    )
    .unwrap_err();

    assert!(error.to_string().contains("expanded size"));
}

#[test]
fn extracts_only_enclosed_regular_files_and_preserves_executable_mode() {
    let root = tempfile::tempdir().unwrap();
    let archive = root.path().join("runtime.zip");
    write_zip(&archive, &[("safe/bin/run", b"runtime", 0o755)]);

    extract_for_test(&archive, &root.path().join("output")).unwrap();

    let output = root.path().join("output/safe/bin/run");
    assert_eq!(fs::read(&output).unwrap(), b"runtime");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            output.metadata().unwrap().permissions().mode() & 0o111,
            0o111
        );
    }
}

#[test]
fn rejects_zip_traversal_and_symbolic_links() {
    let root = tempfile::tempdir().unwrap();
    let traversal = root.path().join("traversal.zip");
    write_zip(&traversal, &[("../escape", b"bad", 0o600)]);
    assert!(extract_for_test(&traversal, &root.path().join("traversal-out")).is_err());
    assert!(!root.path().join("escape").exists());

    let symlink = root.path().join("symlink.zip");
    let mut writer = zip::ZipWriter::new(File::create(&symlink).unwrap());
    writer
        .add_symlink(
            "link",
            "target",
            SimpleFileOptions::default().unix_permissions(0o777),
        )
        .unwrap();
    writer.finish().unwrap();
    assert!(extract_for_test(&symlink, &root.path().join("symlink-out")).is_err());
}
