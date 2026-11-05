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

#[test]
fn an_absolute_entry_name_lands_under_the_destination_not_at_the_path_it_names() {
    let root = tempfile::tempdir().unwrap();
    let escape = root.path().join("escape");
    let archive = root.path().join("absolute.zip");
    write_zip(&archive, &[(escape.to_str().unwrap(), b"bad", 0o600)]);
    // So the test cannot pass by the writer having made the name relative.
    let stored = zip::ZipArchive::new(File::open(&archive).unwrap())
        .unwrap()
        .by_index(0)
        .unwrap()
        .name()
        .unwrap()
        .into_owned();
    assert!(Path::new(&stored).is_absolute(), "{stored}");

    let output = root.path().join("output");
    extract_for_test(&archive, &output).unwrap();

    assert!(!escape.exists());
    let rerooted: PathBuf = escape
        .components()
        .filter(|component| matches!(component, std::path::Component::Normal(_)))
        .collect();
    assert_eq!(fs::read(output.join(rerooted)).unwrap(), b"bad");
}

#[test]
fn rejects_two_entries_whose_paths_differ_only_in_case() {
    let root = tempfile::tempdir().unwrap();
    let archive = root.path().join("case.zip");
    write_zip(
        &archive,
        &[
            ("runtime/LICENSE", b"upper", 0o644),
            ("runtime/license", b"lower", 0o644),
        ],
    );

    let error = extract_for_test(&archive, &root.path().join("output")).unwrap_err();

    assert!(error.to_string().contains("duplicate path"), "{error}");
}

#[test]
fn rejects_a_symbolic_link_recorded_by_an_ms_dos_host() {
    let root = tempfile::tempdir().unwrap();
    let archive = root.path().join("dos-symlink.zip");
    let mut writer = zip::ZipWriter::new(File::create(&archive).unwrap());
    writer
        .add_symlink(
            "link",
            "target",
            SimpleFileOptions::default().unix_permissions(0o777),
        )
        .unwrap();
    writer.finish().unwrap();
    // The central directory's "version made by" host byte, set to MS-DOS.
    let mut bytes = fs::read(&archive).unwrap();
    let central = bytes
        .windows(4)
        .position(|window| window == b"PK\x01\x02")
        .unwrap();
    bytes[central + 5] = 0;
    fs::write(&archive, &bytes).unwrap();
    // The gap this covers: zip reads the entry as a regular file. Should a
    // later release see the link here, this line can go and the test stays.
    assert!(!zip::ZipArchive::new(File::open(&archive).unwrap())
        .unwrap()
        .by_index(0)
        .unwrap()
        .is_symlink());

    let error = extract_for_test(&archive, &root.path().join("output")).unwrap_err();

    assert!(error.to_string().contains("symbolic link"), "{error}");
    assert!(!root.path().join("output/link").exists());
}
