//! The directories a run writes in, and the images it leaves behind.

use super::{
    AcpCallbacks, Engine, EventType, GENERATED_ARTIFACT_DIRECTORY, HostPaths, JsonMap,
    MAX_GENERATED_IMAGE_BYTES, MAX_GENERATED_IMAGES, PathBuf, RunSpec, STANDARD, Uuid, Value,
};

pub(crate) fn host_directory_instructions(cwd: &str) -> String {
    // Encode the path as data: a folder name may contain quotes or newlines.
    let encoded = serde_json::to_string(cwd).expect("strings serialize to JSON");
    format!(
        "\n\n# Native Working Directory\nYour native tools run on this computer in \
         {encoded} (JSON-encoded path). This directory belongs to this conversation \
         and is reused across turns. Use relative paths here for native file and \
         shell tools. Lemma MCP execution tools have their own working directory \
         inside the sandbox, which is not mounted on this computer: never pass a \
         sandbox path to a native tool, or this host path to a sandbox tool. \
         Each tool is given the directory it is supposed to use; use the one you \
         were given rather than a path you assumed. A reported path is not an \
         access grant. Respect tool \
         approvals; access outside this directory requires separate permission."
    )
}

/// The working directory a conversation's provider session lives in.
///
/// Keyed on the conversation, not the run. ACP's `session/load` takes a working
/// directory, and a per-run directory is deleted the moment its run ends — so
/// every follow-up turn asked the agent to resume a session whose cwd no longer
/// existed. Resumption could therefore never succeed, and for `OpenCode` the
/// failed load left the connection unable to open a new session either, which
/// is why the first message answered and the second one did not.
///
/// One directory per conversation also matches what the comment in `acp.rs`
/// already claims: "a Lemma conversation is one provider session".
pub(crate) fn scratch_directory(
    paths: &HostPaths,
    target_id: Uuid,
    conversation_id: Uuid,
) -> PathBuf {
    paths
        .root
        .join("scratch")
        .join(target_id.to_string())
        .join(conversation_id.to_string())
}

pub(crate) fn publish_generated_images(
    scratch_directory: &std::path::Path,
    callbacks: &dyn AcpCallbacks,
) -> anyhow::Result<()> {
    for (object_id, payload) in generated_image_payloads(scratch_directory)? {
        callbacks.event(EventType::AgentMessageChunk, Some(object_id), payload)?;
    }
    // Cleared once published, because the directory around it now outlives the
    // run: it belongs to the conversation so the next turn can resume the
    // session in it. Leaving artifacts behind would republish this turn's
    // images on every later turn.
    let _ = std::fs::remove_dir_all(scratch_directory.join(GENERATED_ARTIFACT_DIRECTORY));
    Ok(())
}

pub(crate) fn prepare_conversation_directory(
    paths: &HostPaths,
    target_id: Uuid,
    conversation_id: Uuid,
) -> anyhow::Result<PathBuf> {
    // Keep the lexical path stable: provider session indexes can distinguish a
    // symlink from its destination even when both name the same directory.
    let path = std::path::absolute(scratch_directory(paths, target_id, conversation_id))?;
    std::fs::create_dir_all(&path)?;
    Ok(path)
}

pub(crate) fn prepare_run_directory(
    paths: &HostPaths,
    target: Uuid,
    spec: &RunSpec,
) -> anyhow::Result<PathBuf> {
    let legacy = scratch_directory(paths, target, spec.conversation_id);
    // Provider session indexes may include the lexical cwd. Never move an
    // existing session's files behind its back during an app upgrade.
    if legacy.exists() || spec.workspace_cwd.is_none() {
        return prepare_conversation_directory(paths, target, spec.conversation_id);
    }
    crate::conversation_directory::prepare(
        &crate::conversation_directory::workspace_root()?,
        target,
        spec.workspace_cwd.as_deref().expect("checked above"),
    )
}

pub(crate) fn generated_image_payloads(
    scratch_directory: &std::path::Path,
) -> anyhow::Result<Vec<(String, JsonMap)>> {
    let directory = scratch_directory.join(GENERATED_ARTIFACT_DIRECTORY);
    let Ok(entries) = std::fs::read_dir(&directory) else {
        return Ok(Vec::new());
    };
    let mut paths = entries
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .collect::<Vec<_>>();
    paths.sort();

    let mut payloads = Vec::new();
    for path in paths {
        if payloads.len() >= MAX_GENERATED_IMAGES {
            break;
        }
        let metadata = std::fs::symlink_metadata(&path)?;
        if !metadata.file_type().is_file() || metadata.len() > MAX_GENERATED_IMAGE_BYTES {
            continue;
        }
        let Some(mime_type) = generated_image_mime_type(&path) else {
            continue;
        };
        let bytes = std::fs::read(&path)?;
        if !generated_image_signature_matches(&bytes, mime_type) {
            continue;
        }
        let filename = path
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("generated-image")
            .to_owned();
        let mut payload = JsonMap::new();
        payload.insert(
            "content".to_owned(),
            serde_json::json!({
                "type": "image",
                "data": STANDARD.encode(bytes),
                "mimeType": mime_type,
            }),
        );
        payload.insert("filename".to_owned(), Value::String(filename));
        payloads.push((format!("generated-image-{}", payloads.len() + 1), payload));
    }
    Ok(payloads)
}

pub(crate) fn generated_image_mime_type(path: &std::path::Path) -> Option<&'static str> {
    match path.extension()?.to_str()?.to_ascii_lowercase().as_str() {
        "png" => Some("image/png"),
        "jpg" | "jpeg" => Some("image/jpeg"),
        "gif" => Some("image/gif"),
        "webp" => Some("image/webp"),
        "avif" => Some("image/avif"),
        _ => None,
    }
}

pub(crate) fn generated_image_signature_matches(bytes: &[u8], mime_type: &str) -> bool {
    match mime_type {
        "image/png" => bytes.starts_with(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg" => bytes.starts_with(b"\xff\xd8\xff"),
        "image/gif" => bytes.starts_with(b"GIF87a") || bytes.starts_with(b"GIF89a"),
        "image/webp" => bytes.len() >= 12 && bytes.starts_with(b"RIFF") && &bytes[8..12] == b"WEBP",
        "image/avif" => {
            bytes.len() >= 16
                && &bytes[4..8] == b"ftyp"
                && (bytes[8..32.min(bytes.len())]
                    .windows(4)
                    .any(|brand| brand == b"avif" || brand == b"avis"))
        }
        _ => false,
    }
}
