//! Where the app code, the tools and the release manifest are -- in a
//! shipped pack, or in a checkout.

use super::*;

pub(crate) fn source_layout() -> io::Result<Option<SourceLayout>> {
    let Some(root) = std::env::var_os("LEMMA_LOCALD_SOURCE_ROOT")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
    else {
        return Ok(None);
    };
    let release_manifest = std::env::var_os("LEMMA_LOCALD_SOURCE_RELEASE_MANIFEST")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .ok_or_else(|| {
            invalid(
                "LEMMA_LOCALD_SOURCE_ROOT needs LEMMA_LOCALD_SOURCE_RELEASE_MANIFEST: a \
                 checkout has no release.json, but its infrastructure and sandbox images \
                 are still pinned ones",
            )
        })?;
    Ok(Some(SourceLayout {
        root,
        release_manifest,
    }))
}

pub(crate) fn packaged_bindings(root: &Path) -> io::Result<Bindings> {
    let python = required_file(
        root,
        "backend Python",
        &[
            "backend/python/bin/python3",
            "backend/python/bin/python",
            "backend/python/python.exe",
        ],
    )?;
    let node = required_file(
        root,
        "frontend Node.js",
        &["frontend/node/bin/node", "frontend/node/node.exe"],
    )?;
    let frontend_launcher = required_file(
        root,
        "frontend launcher",
        &["frontend/frontend-launcher.mjs"],
    )?;
    let frontend_server = required_file(
        root,
        "Next.js standalone server",
        &[
            "frontend/server.js",
            "frontend/app/server.js",
            "frontend/lemma-frontend/server.js",
        ],
    )?;
    let backend_dir = root.join("backend");
    Ok(Bindings {
        python: vec![path_text(&python)?],
        frontend_command: vec![
            path_text(&node)?,
            path_text(&frontend_launcher)?,
            path_text(&frontend_server)?,
        ],
        browser_sdk: backend_dir.join("assets/browser-sdk/lemma-client.js"),
        browser_ui: backend_dir.join("assets/browser-sdk/lemma-ui.js"),
        skills: backend_dir.join("assets/lemma-skills"),
        backend_dir,
        frontend_dir: root.join("frontend"),
        node_env: "production",
        secret_key_provider: "keychain",
    })
}

pub(crate) fn source_bindings(root: &Path) -> io::Result<Bindings> {
    source_bindings_with(root, &resolve_on_path("uv")?, &resolve_on_path("node")?)
}

/// The layout half of source mode, with the toolchain already located.
///
/// Split out so what a checkout *is* can be described without a machine that
/// has `uv` and `node` installed — a CI runner has neither, and a test about
/// where secrets live should not need them.
pub(crate) fn source_bindings_with(root: &Path, uv: &Path, node: &Path) -> io::Result<Bindings> {
    let backend_dir = required_dir(root, "the backend project", "lemma-backend")?;
    let frontend_dir = required_dir(root, "the frontend project", "lemma-frontend")?;
    // The backend owns sandbox provisioning, so one interpreter runs every
    // migration; only the working directory and config name differ.
    let launcher = required_file(
        root,
        "frontend launcher",
        &["desktop/runtime/frontend-launcher.mjs"],
    )?;
    Ok(Bindings {
        // `uv` and `node` come from PATH, which is the point: there is nothing
        // to stage before local mode runs the code being edited. Resolved to
        // absolute paths so locald can identify the processes it spawns.
        python: vec![
            path_text(uv)?,
            "run".to_owned(),
            "--project".to_owned(),
            path_text(&backend_dir)?,
            "python".to_owned(),
        ],
        frontend_command: vec![
            path_text(node)?,
            path_text(&launcher)?,
            "--dev".to_owned(),
            path_text(&frontend_dir)?,
        ],
        browser_sdk: root.join("lemma-typescript/public/lemma-client.js"),
        browser_ui: root.join("lemma-typescript/public/lemma-ui.js"),
        skills: root.join("lemma-skills"),
        backend_dir,
        frontend_dir,
        node_env: "development",
        secret_key_provider: "static",
    })
}

/// Join an interpreter prefix to the arguments that follow it.
///
/// A packaged pack's prefix is one absolute path; a checkout's is
/// `uv run --project <dir> python`. Callers should not have to care which.
pub(crate) fn argv(prefix: &[String], rest: &[&str]) -> Vec<String> {
    let mut command = prefix.to_vec();
    command.extend(rest.iter().map(|value| (*value).to_owned()));
    command
}

/// Resolve a PATH tool to an absolute path, the way a pack's own binaries are.
///
/// Not cosmetic. `record_child` identifies a spawned process with
/// `ps -o comm=`, which reports exactly the argv[0] it was launched with, and
/// then canonicalizes it. Spawned as bare `uv`, that is the string "uv" and the
/// canonicalize fails with ENOENT — so locald tears the process back down with
/// "could not record ownership of backend" and local mode never starts.
pub(crate) fn resolve_on_path(tool: &str) -> io::Result<PathBuf> {
    let path = std::env::var_os("PATH").ok_or_else(|| invalid("PATH is not set"))?;
    // On Windows the thing on PATH is uv.exe or node.exe -- never the bare
    // name -- so joining `tool` alone found neither and source mode could not
    // resolve its toolchain at all.
    #[cfg(windows)]
    let names: Vec<String> = ["", ".exe", ".cmd", ".bat"]
        .iter()
        .map(|suffix| format!("{tool}{suffix}"))
        .collect();
    #[cfg(not(windows))]
    let names: Vec<String> = vec![tool.to_owned()];

    std::env::split_paths(&path)
        .flat_map(|directory| {
            names
                .iter()
                .map(|name| directory.join(name))
                .collect::<Vec<_>>()
        })
        .find(|candidate| candidate.is_file())
        .ok_or_else(|| {
            invalid(format!(
                "source mode needs {tool} on PATH; it runs the checkout directly"
            ))
        })
        .and_then(|candidate| canonicalize_for_children(&candidate))
}

pub(crate) fn required_dir(root: &Path, label: &str, relative: &str) -> io::Result<PathBuf> {
    let candidate = root.join(relative);
    if candidate.is_dir() {
        return canonicalize_for_children(&candidate);
    }
    Err(invalid(format!(
        "source checkout is missing {label}: {}",
        candidate.display()
    )))
}

pub(crate) fn required_file(root: &Path, label: &str, candidates: &[&str]) -> io::Result<PathBuf> {
    for relative in candidates {
        let candidate = root.join(relative);
        if candidate.is_file() {
            return canonicalize_for_children(&candidate);
        }
    }
    Err(io::Error::new(
        io::ErrorKind::NotFound,
        format!(
            "native host pack is missing {label}; expected one of {}",
            candidates.join(", ")
        ),
    ))
}

pub(crate) fn read_json(path: &Path, label: &str) -> io::Result<Value> {
    serde_json::from_slice(&fs::read(path)?)
        .map_err(|error| invalid(format!("invalid {label}: {error}")))
}

pub(crate) fn required_string(value: &Value, key: &str, label: &str) -> io::Result<String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .map(str::to_owned)
        .ok_or_else(|| invalid(format!("{label} has no {key}")))
}

pub(crate) fn pull_ref(value: Option<&Value>, label: &str) -> io::Result<String> {
    let reference = match value {
        Some(Value::String(value)) => value.clone(),
        Some(Value::Object(value)) => {
            let base = value.get("ref").and_then(Value::as_str).unwrap_or_default();
            let digest = value.get("digest").and_then(Value::as_str);
            match digest {
                Some(digest) if !base.contains('@') => format!("{base}@{digest}"),
                _ => base.to_owned(),
            }
        }
        _ => String::new(),
    };
    if reference.is_empty() || reference.bytes().any(|byte| byte.is_ascii_whitespace()) {
        return Err(invalid(format!(
            "native release manifest has no valid {label}"
        )));
    }
    Ok(reference)
}

/// Take Windows' extended-length prefix back off a path, when it is safe to.
///
/// `\\?\C:\...` is what `canonicalize` returns on Windows. Rust handles it
/// and so does `CreateProcess`, which is why it went unnoticed -- but these
/// paths are handed to *other programs*, and the prefix is not universal.
/// Node's module resolver reads `\\?\C:\Users\...` as a UNC path: server
/// `?`, share `C:`. It then calls `lstat` on `C:` and dies with
/// `EISDIR: illegal operation on a directory`. That is the whole of why the
/// frontend could not start on Windows, on a machine where everything else in
/// the stack had come up.
///
/// Only a plain drive path is stripped. `\\?\UNC\server\share` is a real
/// UNC path and keeps its prefix, and so does anything the caller finds does
/// not resolve without it -- a path long enough to need it is not decoration.
pub(crate) fn without_verbatim_prefix(text: &str) -> Option<&str> {
    let rest = text.strip_prefix(r"\\?\")?;
    let bytes = rest.as_bytes();
    // A drive path, not `UNC\...`: exactly `<letter>:` and then a separator.
    if bytes.len() >= 3 && bytes[0].is_ascii_alphabetic() && bytes[1] == b':' && bytes[2] == b'\\' {
        Some(rest)
    } else {
        None
    }
}

/// `canonicalize`, in a form the programs this spawns can read.
///
/// Every path in the generated manifest descends from one of these roots, so
/// the prefix removed here is removed from all of them.
pub(crate) fn canonicalize_for_children(path: &Path) -> io::Result<PathBuf> {
    let canonical = path.canonicalize()?;
    let Some(text) = canonical.to_str() else {
        return Ok(canonical);
    };
    let Some(plain) = without_verbatim_prefix(text) else {
        return Ok(canonical);
    };
    let plain = PathBuf::from(plain);
    // Keep the prefix if the path genuinely needs it to resolve.
    if plain.exists() {
        Ok(plain)
    } else {
        Ok(canonical)
    }
}

pub(crate) fn path_text(path: &Path) -> io::Result<String> {
    path.to_str().map(str::to_owned).ok_or_else(|| {
        invalid(format!(
            "runtime path is not valid UTF-8: {}",
            path.display()
        ))
    })
}
