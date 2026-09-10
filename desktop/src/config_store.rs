use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::Path;
use std::sync::Mutex;

use serde_json::{json, Value};

static WRITER: Mutex<()> = Mutex::new(());

pub fn update(path: &Path, update: impl FnOnce(&mut Value)) -> io::Result<()> {
    let _guard = WRITER.lock().map_err(|_| {
        io::Error::other("desktop configuration writer was interrupted; restart Lemma")
    })?;
    let mut config = match fs::read(path) {
        Ok(bytes) => serde_json::from_slice::<Value>(&bytes)
            .ok()
            .filter(Value::is_object)
            .ok_or_else(|| {
                io::Error::other(
                    "desktop configuration is damaged; open Recovery to repair this installation",
                )
            })?,
        Err(error) if error.kind() == io::ErrorKind::NotFound => json!({}),
        Err(error) => return Err(error),
    };
    update(&mut config);
    if !config.is_object() {
        return Err(io::Error::other("desktop configuration must be an object"));
    }
    let directory = path
        .parent()
        .ok_or_else(|| io::Error::other("configuration has no parent directory"))?;
    fs::create_dir_all(directory)?;
    let temporary = path.with_extension(format!("json.next-{}", std::process::id()));
    match fs::remove_file(&temporary) {
        Ok(()) => (),
        Err(error) if error.kind() == io::ErrorKind::NotFound => (),
        Err(error) => return Err(error),
    }
    let result = (|| {
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&temporary)?;
        let serialized = serde_json::to_vec_pretty(&config).map_err(io::Error::other)?;
        file.write_all(&serialized)?;
        file.sync_all()?;
        drop(file);
        replace(&temporary, path)?;
        #[cfg(unix)]
        fs::File::open(directory)?.sync_all()?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(temporary);
    }
    result
}

#[cfg(not(windows))]
pub fn replace(source: &Path, destination: &Path) -> io::Result<()> {
    fs::rename(source, destination)
}

#[cfg(windows)]
pub fn replace(source: &Path, destination: &Path) -> io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let source: Vec<u16> = source.as_os_str().encode_wide().chain(Some(0)).collect();
    let destination: Vec<u16> = destination
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect();
    let result = unsafe {
        MoveFileExW(
            source.as_ptr(),
            destination.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if result == 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Barrier};

    #[test]
    fn concurrent_runtime_and_window_updates_preserve_every_field() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("desktop-config.json");
        update(&path, |config| {
            config["installedRuntime"] = json!({"release": "1.2.3"})
        })
        .unwrap();
        let barrier = Arc::new(Barrier::new(16));
        std::thread::scope(|scope| {
            for index in 0..16 {
                let barrier = barrier.clone();
                let path = &path;
                scope.spawn(move || {
                    barrier.wait();
                    update(path, |config| {
                        config[format!("window-{index}")] = json!(index)
                    })
                    .unwrap();
                });
            }
        });
        let saved: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        assert_eq!(saved["installedRuntime"]["release"], "1.2.3");
        for index in 0..16 {
            assert_eq!(saved[format!("window-{index}")], index);
        }
        assert_eq!(fs::read_dir(root.path()).unwrap().count(), 1);
    }

    #[test]
    fn damaged_configuration_is_preserved_instead_of_replaced_with_defaults() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("desktop-config.json");
        for bytes in [b"{\"installedRuntime\":".as_slice(), b"null", b"[]", b"1"] {
            fs::write(&path, bytes).unwrap();
            assert!(update(&path, |_| panic!("damaged config must not be edited")).is_err());
            assert_eq!(fs::read(&path).unwrap(), bytes);
        }
    }

    #[test]
    fn invalid_update_preserves_the_last_committed_configuration() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("desktop-config.json");
        update(&path, |value| value["mode"] = json!("local")).unwrap();
        let before = fs::read(&path).unwrap();
        assert!(update(&path, |value| *value = Value::Null).is_err());
        assert_eq!(fs::read(&path).unwrap(), before);
    }

    #[test]
    fn failed_staging_keeps_the_previous_configuration_and_can_be_retried() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("desktop-config.json");
        update(&path, |value| value["mode"] = json!("local")).unwrap();
        let temporary = path.with_extension(format!("json.next-{}", std::process::id()));
        fs::create_dir(&temporary).unwrap();
        assert!(update(&path, |value| value["mode"] = json!("hosted")).is_err());
        let before: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        assert_eq!(before["mode"], "local");
        fs::remove_dir(&temporary).unwrap();
        update(&path, |value| value["mode"] = json!("hosted")).unwrap();
        let after: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        assert_eq!(after["mode"], "hosted");
    }

    #[cfg(unix)]
    #[test]
    fn replacement_does_not_follow_a_stale_staging_symlink_and_is_private() {
        use std::os::unix::fs::{symlink, PermissionsExt};
        let root = tempfile::tempdir().unwrap();
        let external = root.path().join("project.txt");
        fs::write(&external, b"project data").unwrap();
        let path = root.path().join("desktop-config.json");
        let temporary = path.with_extension(format!("json.next-{}", std::process::id()));
        symlink(&external, &temporary).unwrap();
        update(&path, |value| value["mode"] = json!("local")).unwrap();
        assert_eq!(fs::read(external).unwrap(), b"project data");
        assert_eq!(
            fs::metadata(path).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }
}
