use serde_json::json;

use super::*;

#[test]
fn endpoint_uses_the_run_scoped_configuration() {
    let run_id = Uuid::new_v4();
    let endpoint = endpoint_from_mcp(
        run_id,
        &json!({
            "url": "https://lemma.example/mcp",
            "authorization": "Bearer secret"
        }),
    )
    .unwrap();
    assert_eq!(endpoint.run_id, run_id);
    assert_eq!(endpoint.url, "https://lemma.example/mcp");
}

#[test]
fn endpoint_rejects_a_missing_credential() {
    let result = endpoint_from_mcp(Uuid::new_v4(), &json!({"url": "https://lemma.example/mcp"}));
    assert!(result.is_err());
}
