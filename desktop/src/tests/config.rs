use super::*;

/// Cloud and local must never share a store, and neither may drift between
/// launches.
///
/// The identifiers are constants precisely so this can be asserted. If one
/// were ever derived from something per-run, a restart would look like a
/// different server and silently sign the user out of the one they kept.
#[cfg(target_os = "macos")]
#[test]
fn each_server_gets_its_own_session_store() {
    let hosted = super::session_partition_id("hosted");
    let local = super::session_partition_id("local");
    assert_ne!(hosted, local);
    // Stable across calls, which is what makes a session survive a restart.
    assert_eq!(hosted, super::session_partition_id("hosted"));
    assert_eq!(local, super::session_partition_id("local"));
    // Anything that is not the hosted server is the local one. "undecided"
    // reaches here on a first launch that has not been answered yet, and it
    // must not land in the cloud store.
    assert_eq!(local, super::session_partition_id("undecided"));
    assert_eq!(local, super::session_partition_id(""));
}

/// Each channel must read the feed that actually carries its builds.
///
/// GitHub resolves `releases/latest` to the newest *non-prerelease*.
/// Nightlies are prereleases, so a nightly pointed there sees either
/// nothing or a stable build -- and an update path that only ever runs on
/// release day is one nobody has tested. The nightly tag is rewritten in
/// place so the address stays put while its contents move; that fixed
/// address is the whole reason a nightly feed can be called durable.
#[test]
fn each_channel_reads_its_own_feed() {
    let nightly = updater_endpoints("nightly");
    let stable = updater_endpoints("stable");

    assert_eq!(nightly.len(), 1);
    assert!(
        nightly[0].ends_with("/releases/download/desktop-nightly/latest.json"),
        "a nightly must read a tag that does not move: {nightly:?}",
    );
    assert!(
        !nightly[0].contains("/releases/latest/"),
        "`releases/latest` skips prereleases, so it never lists a nightly",
    );

    assert_eq!(stable, updater_endpoints("dev"), "dev falls back to stable");
    assert!(stable[0].ends_with("/releases/latest/download/latest.json"));

    // Whatever they are, the plugin has to be able to parse them, and a
    // typo here would otherwise be a silently empty endpoint list.
    for channel in ["stable", "nightly", "dev"] {
        for endpoint in updater_endpoints(channel) {
            assert!(
                tauri::Url::parse(&endpoint).is_ok(),
                "{channel} endpoint is not a URL: {endpoint}",
            );
        }
    }
}

/// The documented endpoints are the endpoints.
///
/// `docs/installation.md` tells a reader exactly what leaves their machine when
/// they open Local settings, and a reader has no way to check it. A URL that
/// moves in code and not in the doc turns that section from an assurance into a
/// claim -- which is what the register found: this traffic was not written down
/// anywhere at all.
///
/// Both directions on purpose. A doc naming an address the app no longer uses
/// is as wrong as a doc missing one it does.
#[test]
fn the_installation_guide_names_the_addresses_this_build_asks() {
    let guide = include_str!("../../../docs/installation.md").replace("\r\n", "\n");
    let section = guide
        .split("### Checking for updates")
        .nth(1)
        .expect("the guide documents where updates are checked");
    let section = section.split("\n## ").next().unwrap_or(section);

    let stable = updater_endpoints("stable");
    let nightly = updater_endpoints("nightly");
    assert_eq!((stable.len(), nightly.len()), (1, 1));
    let (stable, nightly) = (&stable[0], &nightly[0]);

    // Everything before the path: scheme, host and owner. The guide writes it
    // out once, in the stable URL, and elides it with `...` in the nightly one.
    let shared = stable
        .rfind("/releases/")
        .map(|at| &stable[..at])
        .expect("a stable endpoint is a releases URL");
    // Which is only honest if the nightly really does share it. Comparing the
    // two on their suffixes alone would accept a doc that had quietly moved
    // one of them to another host.
    assert!(
        nightly.starts_with(shared),
        "the guide elides {shared} from the nightly endpoint, and {nightly} \
         does not begin with it",
    );

    // In full, so a documented `https://elsewhere.example/...` with a matching
    // tail is not read as naming this one.
    assert!(
        section.contains(stable.as_str()),
        "docs/installation.md does not name the stable update endpoint {stable}",
    );
    let elided = format!("...{}", &nightly[shared.len()..]);
    assert!(
        section.contains(&elided),
        "docs/installation.md does not name the nightly update endpoint, \
         written as {elided}",
    );

    // And nothing it names has gone away. Token by token rather than line by
    // line: one of the two addresses is written inside a sentence.
    for token in section.split_whitespace() {
        // Prose punctuation, so a sentence that ends on a URL is not read as
        // naming a different one.
        let named = token
            .trim_matches(|c: char| !c.is_ascii_graphic() || "`,()\"".contains(c))
            .trim_end_matches('.');
        if !named.contains("latest.json") || !named.contains("releases/") {
            continue;
        }
        // An elided address is expanded against the prefix the guide elided,
        // so what is compared is a whole URL either way.
        let full = match named.strip_prefix("...") {
            Some(rest) => format!("{shared}{rest}"),
            None => named.to_owned(),
        };
        assert!(
            [stable, nightly].contains(&&full),
            "docs/installation.md names {full}, which no channel reads",
        );
    }
}

/// The updater's transport policy is not weakened, and the artifact flag
/// stays out of the base config.
/// The key an installed app verifies with is real, and matches its own id.
///
/// `pubkey: ""` is not a permissive setting. `verify_signature` decodes it
/// and fails, so a build carrying an empty key offers an update, downloads
/// it, and then cannot verify a thing -- and `updates_enabled()` exists to
/// keep such a build from offering one at all.
///
/// What was missing is the assertion that the key is *there*. The release
/// workflow required the private half and never looked at the public one,
/// and the config test asserted the endpoint and the CSP and not this. So a
/// release could ship with no key, which is the state this repo was in.
#[test]
fn the_shipped_public_key_is_a_real_minisign_key() {
    use base64::Engine as _;

    let config: Value =
        serde_json::from_str(include_str!("../../tauri.conf.json")).expect("config parses");
    let pubkey = config
        .pointer("/plugins/updater/pubkey")
        .and_then(Value::as_str)
        .expect("the updater declares a pubkey field");

    assert!(
        !pubkey.trim().is_empty(),
        "no public key: every install would download an update and then refuse it",
    );
    assert!(
        updater_key_configured(),
        "the gate that reads this must agree with it",
    );

    let decoded = base64::engine::general_purpose::STANDARD
        .decode(pubkey)
        .expect("the pubkey is base64");
    let text = String::from_utf8(decoded).expect("a minisign key is text");
    let mut lines = text.lines();
    let comment = lines.next().unwrap_or_default();
    assert!(
        comment.starts_with("untrusted comment:") && comment.contains("public key"),
        "this is not a minisign public key: {comment}",
    );

    // The body carries the algorithm and the key id the signature must
    // name. A truncated paste passes every check above and none of these.
    let body = base64::engine::general_purpose::STANDARD
        .decode(lines.next().expect("a key line follows the comment"))
        .expect("the key line is base64");
    assert_eq!(&body[..2], b"Ed", "only Ed25519 keys are signed against");
    assert_eq!(body.len(), 42, "a minisign public key is 2 + 8 + 32 bytes");
    let key_id = &body[2..10];
    assert!(
        key_id.iter().any(|byte| *byte != 0),
        "a zero key id is a placeholder, not a key",
    );

    // And it is not a secret key committed by mistake, which is the same
    // file format with a different comment.
    assert!(
        !text.contains("secret key"),
        "the SECRET key is in this config; rotate it immediately",
    );
}

#[test]
fn switching_connection_says_what_it_is_about_to_do() {
    let (title, body, confirm) = connection_switch_prompt("hosted", false);
    assert_eq!(title, format!("Run Lemma on {THIS_COMPUTER}?"));
    assert_eq!(confirm, "Start Local");
    // The ninety-second health gate is the whole reason this prompt exists:
    // the press used to be followed by silence for minutes.
    assert!(body.contains("takes a few minutes"), "{body}");

    let (title, body, confirm) = connection_switch_prompt("local", true);
    assert_eq!(title, "Use the hosted workspace?");
    assert_eq!(confirm, "Use Hosted");
    assert!(
        body.contains(&format!("keeps running on {THIS_COMPUTER}")),
        "{body}"
    );
    // Leaving must never read as destroying: the pods stay.
    assert!(body.contains("stay where they are"), "{body}");
}

#[test]
fn local_models_are_reached_through_a_provider_endpoint_not_an_app_owned_server() {
    let html = include_str!("../../ui/control.html").replace("\r\n", "\n");
    let script = include_str!("../../ui/control.js").replace("\r\n", "\n");

    // Ollama and LM Studio are the supported local-model path: they are
    // ordinary OpenAI-compatible endpoints the user already runs, so they
    // only prefill the provider form and never give Lemma a model process
    // of its own to install, supervise, or free memory for.
    assert!(html.contains("data-preset=\"ollama\""));
    assert!(html.contains("data-preset=\"lmstudio\""));
    assert!(script.contains("http://127.0.0.1:11434/v1"));
    assert!(script.contains("http://127.0.0.1:1234/v1"));
    assert!(!script.contains("local_ai_action"));
    assert!(!html.contains("data-page=\"models\""));
}

#[test]
fn the_default_model_is_chosen_from_the_providers_own_list() {
    let html = include_str!("../../ui/control.html").replace("\r\n", "\n");
    let script = include_str!("../../ui/control.js").replace("\r\n", "\n");

    // Typing a model id from memory was the old contract and the reason a
    // correct provider could still be applied with a model it does not
    // serve. The default is now a <select> populated by the probe, and the
    // free-text list it replaced must not come back.
    assert!(html.contains("<select id=\"ai-model\">"));
    assert!(!html.contains("id=\"ai-models\""));
    // The probe answers the press that made it, rather than broadcasting to
    // whichever page happens to be listening.
    assert!(script.contains("const models = await invoke(\"discover_provider_models\""));
    assert!(!script.contains("config.models"));
}

#[test]
fn local_mode_still_denies_a_local_destination_that_is_not_ours() {
    // The allowance above must not become a hole: an arbitrary loopback
    // port is still refused in local mode.
    assert_eq!(
        navigation_disposition(
            &tauri::Url::parse("http://127.0.0.1:9999/").unwrap(),
            "local",
            "http://app.lemma.localhost:52501",
            "http://app.lemma.localhost:52502",
        ),
        NavigationDisposition::Deny
    );
}
