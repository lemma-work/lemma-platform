use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use tempfile::tempdir;

use super::{GENERATED_ARTIFACT_DIRECTORY, capabilities_from_acp, generated_image_payloads};

#[test]
fn maps_structured_acp_capabilities_without_string_heuristics() {
    let capabilities = capabilities_from_acp(&serde_json::json!({
        "loadSession": true,
        "promptCapabilities": {"image": true},
        "sessionCapabilities": {"resume": {}, "close": {}},
    }));
    assert!(capabilities.load_session);
    assert!(capabilities.resume_session);
    assert!(capabilities.close_session);
    assert!(capabilities.images);
    assert!(!capabilities.durable_session_recovery);
}

#[test]
fn generated_images_are_bounded_to_the_explicit_artifact_directory() {
    let root = tempdir().unwrap();
    let artifacts = root.path().join(GENERATED_ARTIFACT_DIRECTORY);
    std::fs::create_dir_all(&artifacts).unwrap();
    let png = b"\x89PNG\r\n\x1a\nimage";
    std::fs::write(artifacts.join("poster.png"), png).unwrap();
    std::fs::write(root.path().join("private.png"), png).unwrap();
    std::fs::write(artifacts.join("notes.txt"), b"not an image").unwrap();

    let payloads = generated_image_payloads(root.path()).unwrap();

    assert_eq!(payloads.len(), 1);
    assert_eq!(payloads[0].0, "generated-image-1");
    assert_eq!(payloads[0].1["filename"], "poster.png");
    let content = payloads[0].1["content"].as_object().unwrap();
    assert_eq!(content["mimeType"], "image/png");
    assert_eq!(
        STANDARD.decode(content["data"].as_str().unwrap()).unwrap(),
        png
    );
}
