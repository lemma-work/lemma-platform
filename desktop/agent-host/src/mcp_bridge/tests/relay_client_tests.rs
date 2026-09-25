use super::super::*;

/// A relay that hangs up on every connection right after its hello, the way
/// one does when the Agent Host restarts between two tool calls.
async fn hanging_up_relay(paths: &HostPaths, target_id: Uuid) -> tokio::task::JoinHandle<()> {
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
        .await
        .unwrap();
    let endpoint = RelayEndpoint {
        port: listener.local_addr().unwrap().port(),
        token: "relay-token".into(),
    };
    lemma_private_file::write_atomic(
        &endpoint_path(paths, target_id),
        &serde_json::to_vec(&endpoint).unwrap(),
    )
    .unwrap();
    tokio::spawn(async move {
        while let Ok((stream, _)) = listener.accept().await {
            let mut lines = BufReader::new(stream).lines();
            let _hello = lines.next_line().await;
        }
    })
}

/// A request made after the relay went away must end, not wait on a
/// connection whose reader already gave up.
#[tokio::test]
async fn a_request_after_the_relay_hung_up_ends() {
    let directory = tempfile::tempdir().unwrap();
    let paths = HostPaths::under(directory.path());
    paths.ensure().unwrap();
    let target_id = Uuid::new_v4();
    let _relay = hanging_up_relay(&paths, target_id).await;
    let client = RelayClient::connect(&paths, target_id, Uuid::new_v4())
        .await
        .unwrap();
    // Long enough for the relay to have hung up on the first connection.
    tokio::time::sleep(Duration::from_millis(200)).await;
    let answered = tokio::time::timeout(
        Duration::from_secs(20),
        client.request("tools/list", json!({})),
    )
    .await
    .expect("the request hung on a dead connection");
    assert!(answered.is_err());
}
