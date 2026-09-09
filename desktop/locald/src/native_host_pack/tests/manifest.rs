//! What the rendered manifest says.

use super::*;

#[test]
fn renders_packaged_managed_runtime_without_compatibility_supervisor() {
    let root = tempdir().unwrap();
    let pack = root.path().join("pack");
    fs::create_dir_all(&pack).unwrap();
    fixture(&pack);
    let paths = LocalPaths::new(root.path().join("locald"));
    paths.ensure().unwrap();
    let output = prepare(
        &paths,
        &pack,
        ManagedManifestMaterial {
            postgres_password: "a".repeat(64),
            redis_password: "b".repeat(64),
            bridge_executable: PathBuf::from("/signed/lemma-runtime"),
        },
        &mut Vec::new(),
    )
    .unwrap();
    let manifest: Value = serde_json::from_slice(&fs::read(output).unwrap()).unwrap();
    let frontend_port = manifest["managed_runtime"]["ports"]["frontend"]
        .as_u64()
        .unwrap();
    let backend_port = manifest["managed_runtime"]["ports"]["backend"]
        .as_u64()
        .unwrap();
    let runtime_instance = manifest["services"][0]["env"]["LEMMA_RUNTIME_INSTANCE_ID"]
        .as_str()
        .unwrap();

    assert_eq!(manifest["release"], "6.2.0");
    assert_eq!(manifest["services"].as_array().unwrap().len(), 2);
    // One migration chain: the manager's own database is gone. The
    // connector catalog is seeded straight after it, because a packaged
    // install has no connectors at all until something does.
    assert_eq!(manifest["setup"].as_array().unwrap().len(), 2);
    assert_eq!(manifest["setup"][0]["id"], "migrations");
    assert_eq!(manifest["setup"][1]["id"], "connector-catalog");
    // Seeding reaches the network when a Composio key is set, and a
    // workspace must not fail to start because a third-party catalog was
    // unreachable.
    assert_eq!(manifest["setup"][1]["optional"], true);
    assert_ne!(manifest["setup"][0]["optional"], serde_json::json!(true));
    // No --provider flag: native always, Composio only when a key is set,
    // which is what lets adding a key later work on the next start.
    let catalog = manifest["setup"][1]["command"].as_array().unwrap();
    assert!(catalog.iter().all(|arg| arg.as_str() != Some("--provider")));
    assert_eq!(
        manifest["services"][0]["env"]["WORKSPACE_PROVIDER"],
        "lemma_local"
    );
    assert!(!manifest["services"][0]["command"]
        .as_array()
        .unwrap()
        .iter()
        .any(|argument| argument == "--no-access-log"));
    assert_eq!(
        manifest["services"][0]["env"]["WORKSPACE_CALLBACK_API_URL"],
        format!("http://host.lemma.internal:{backend_port}")
    );
    assert!(manifest["services"][0]["env"]
        .get("FUNCTION_RUNTIME_SECRET")
        .is_none());
    assert_eq!(
        manifest["services"][0]["env"]["DOCUMENT_PROCESSOR"],
        "xberg"
    );
    assert_eq!(
        manifest["services"][0]["env"]["HOME"],
        path_text(&paths.root.join("state").join("home")).unwrap()
    );
    assert_eq!(
        manifest["services"][0]["env"]["XDG_CACHE_HOME"],
        path_text(&paths.root.join("state").join("cache")).unwrap()
    );
    assert_eq!(
        manifest["services"][0]["env"]["TLDEXTRACT_CACHE"],
        path_text(&paths.root.join("state").join("cache").join("tldextract")).unwrap()
    );
    assert_eq!(
        manifest["services"][0]["env"]["SUPERTOKENS_TLDEXTRACT_DISABLE_HTTP"],
        "1"
    );
    assert_eq!(
        manifest["services"][0]["env"]["LOCAL_EMBEDDING_CACHE_DIR"],
        path_text(&paths.root.join("state").join("cache").join("fastembed")).unwrap()
    );
    assert_eq!(
        manifest["services"][0]["env"]["SECRET_KEY_PROVIDER"],
        "keychain"
    );
    // A packaged install must never carry the key in its own config; the
    // keychain is the point.
    assert!(manifest["services"][0]["env"]
        .get("SECRET_ENCRYPTION_KEY")
        .is_none());
    assert!(paths
        .root
        .join("state")
        .join("cache")
        .join("tldextract")
        .is_dir());
    assert!(paths
        .root
        .join("state")
        .join("cache")
        .join("fastembed")
        .is_dir());
    assert_eq!(
        manifest["services"][0]["env"]["AUTH_EMAIL_VERIFICATION_REQUIRED"],
        "false"
    );
    assert_eq!(
        manifest["services"][0]["env"]["LOCAL_HTTP_ACCESS_LOGS_ENABLED"],
        "true"
    );
    assert_eq!(
        manifest["services"][0]["env"]["AUTH_ABUSE_PROTECTION_ENABLED"],
        "false"
    );
    assert_eq!(
        manifest["services"][0]["env"]["DESKTOP_AUTH_CREATE_LIMIT"],
        "0"
    );
    // Wide enough to cover the app subdomains. Host-only here is what made
    // every pod app load unauthenticated; see the note beside the value.
    assert_eq!(
        manifest["services"][0]["env"]["SESSION_COOKIE_DOMAIN"],
        LocalDomain::from_env().cookie_domain()
    );
    assert_eq!(
        manifest["services"][0]["env"]["API_URL"],
        format!(
            "http://{}:{backend_port}",
            LocalDomain::from_env().frontend_host()
        )
    );
    // And the browser-visible one is NOT widened with it. These cookies are
    // written by `document.cookie`, so a shared domain lets a pod app
    // overwrite the workspace's session state and sign the user out.
    assert_eq!(
        manifest["services"][1]["env"]["NEXT_PUBLIC_SESSION_TOKEN_DOMAIN"],
        ""
    );
    assert_eq!(
        manifest["services"][1]["env"]["NEXT_PUBLIC_API_URL"],
        format!(
            "http://{}:{backend_port}",
            LocalDomain::from_env().frontend_host()
        )
    );
    assert_eq!(
        manifest["services"][0]["env"]["WORKSPACE_LOCAL_CALLBACK_URL"],
        format!("http://host.lemma.internal:{backend_port}")
    );
    assert_eq!(
        manifest["services"][0]["env"]["WORKSPACE_IMAGE"],
        "workspace@sha256:workspace"
    );
    assert_eq!(
        manifest["services"][0]["env"]["FUNCTION_IMAGE"],
        "function@sha256:function"
    );
    assert_eq!(
        manifest["managed_runtime"]["images"]["postgres"],
        "postgres@sha256:postgres"
    );
    assert!(frontend_port >= 49_152);
    assert!(backend_port >= 49_152);
    assert_ne!(frontend_port, backend_port);
    assert_eq!(
        manifest["services"][0]["env"]["LOCAL_EMBEDDING_STARTUP_MODE"],
        "background"
    );
    assert_eq!(
        manifest["services"][1]["env"]["NEXT_PUBLIC_LEMMA_RUNTIME_INSTANCE_ID"],
        runtime_instance
    );
    assert_eq!(
        manifest["services"][0]["health"]["expected_body"],
        runtime_instance
    );
    assert_eq!(
        manifest["services"][1]["health"]["expected_body"],
        runtime_instance
    );
    assert!(paths.root.join("host.secrets.json").is_file());
}

#[test]
fn managed_infrastructure_images_must_be_digest_pinned() {
    let root = tempdir().unwrap();
    let pack = root.path().join("pack");
    fs::create_dir_all(&pack).unwrap();
    fixture(&pack);
    let mut release: Value = read_json(&pack.join("release.json"), "fixture").unwrap();
    release["infra"]["redis"] = Value::String("redis:latest".into());
    fs::write(
        pack.join("release.json"),
        serde_json::to_vec(&release).unwrap(),
    )
    .unwrap();
    let paths = LocalPaths::new(root.path().join("locald"));
    paths.ensure().unwrap();
    let error = build(
        &paths,
        &pack,
        &ManagedManifestMaterial {
            postgres_password: "a".repeat(64),
            redis_password: "b".repeat(64),
            bridge_executable: PathBuf::from("/signed/lemma-runtime"),
        },
        load_or_allocate(&paths).unwrap(),
        None,
        &mut Vec::new(),
        &LocalDomain::default(),
    )
    .unwrap_err();
    assert!(error.to_string().contains("Redis image must be pinned"));
}
