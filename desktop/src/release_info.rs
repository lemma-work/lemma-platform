use super::*;

/// Which build this is, for support and for whether it self-updates.
///
/// A compile-time stamp rather than a version suffix. A suffix would have to
/// travel through the runtime manifest, the per-version install directory and
/// the branch-test version gate, none of which care which channel a build came
/// from. This answers "what are you running?" -- which had no answer at all,
/// because a nightly and a release both report `0.7.0` with the same bundle
/// identifier.
///
/// Unrecognised stamps are development builds and cannot update themselves.
pub(crate) fn release_channel() -> &'static str {
    channel_of(option_env!("LEMMA_RELEASE_CHANNEL"))
}

/// The channel a build stamp names, or `dev`.
///
/// Split out so the mapping can be asserted. Testing `release_channel()`
/// directly proves nothing: it reads a compile-time stamp that a test build
/// never has, so every assertion about it holds by construction.
pub(crate) fn channel_of(stamp: Option<&str>) -> &'static str {
    match stamp {
        Some("stable") => "stable",
        Some("nightly") => "nightly",
        _ => "dev",
    }
}

pub(crate) fn build_commit() -> Option<&'static str> {
    option_env!("LEMMA_BUILD_SHA")
}

/// Whether this build may update itself in place.
///
/// Stable and nightly release builds use separate feeds and the same signed
/// update mechanism. Local development builds cannot replace themselves.
pub(crate) fn updates_enabled() -> bool {
    updates_allowed(
        release_channel(),
        cfg!(debug_assertions),
        updater_key_configured(),
    )
}

/// Whether a build with these three properties may update itself.
///
/// A debug build is not a release, and a build with no public key cannot verify
/// what it downloads. Both remain disqualifying.
///
/// Nightly used to be, on two grounds stated here: it had no durable feed and
/// was never given the signing key. Both are now false. Nightly builds are
/// signed with the same key as a release -- so the committed public key
/// verifies them, and `key_configured` is a real check for them too -- and they
/// publish to a tag that does not move, which is what makes a feed durable.
///
/// The point is not convenience. An update path nobody walks until release day
/// is an update path nobody has tested; letting nightly update to nightly means
/// the mechanism is exercised continuously, by people who can report what broke
/// rather than discovering it in a stable rollout.
pub(crate) fn updates_allowed(channel: &str, debug: bool, key_configured: bool) -> bool {
    matches!(channel, "stable" | "nightly") && !debug && key_configured
}

/// `updater_endpoints` for this build, parsed, for the plugin's builder.
///
/// A malformed constant here would be a build-time mistake, not a runtime
/// condition, so an unparseable entry is dropped rather than failing the check
/// -- leaving the plugin with whatever `tauri.conf.json` configured, which is
/// the stable feed.
pub(crate) fn parsed_updater_endpoints() -> Vec<tauri::Url> {
    updater_endpoints(release_channel())
        .into_iter()
        .filter_map(|endpoint| tauri::Url::parse(&endpoint).ok())
        .collect()
}

/// Where this build looks for its update feed.
///
/// Stable reads the `latest` release, which GitHub resolves to the newest
/// non-prerelease. Nightlies are prereleases and never appear there, so a
/// nightly pointed at it would either see nothing or be offered a *stable*
/// build -- neither of which tests anything. They read a tag that is rewritten
/// in place instead, so the address stays constant while its contents move.
///
/// Returned rather than baked into `tauri.conf.json` because the config is one
/// file shared by every build, and the channel is a compile-time stamp. One
/// place decides, and the tests below can ask it.
pub(crate) fn updater_endpoints(channel: &str) -> Vec<String> {
    const OWNER_REPO: &str = "lemma-work/lemma-platform";
    match channel {
        "nightly" => vec![format!(
            "https://github.com/{OWNER_REPO}/releases/download/desktop-nightly/latest.json"
        )],
        _ => vec![format!(
            "https://github.com/{OWNER_REPO}/releases/latest/download/latest.json"
        )],
    }
}

/// Whether this build carries a public key that can verify an update.
///
/// `tauri.conf.json` ships `"pubkey": ""` until the signing keypair exists, and
/// an empty key is not a permissive setting -- `verify_signature` decodes it and
/// fails, so *every* install fails. Without this check the app offers an update,
/// stops the user's daemon to make room for it, and only then discovers it
/// cannot verify a thing.
///
/// Read from the committed config at compile time, so a build either has a key
/// or does not; there is nothing to get out of sync at runtime.
pub(crate) fn updater_key_configured() -> bool {
    static CONFIGURED: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *CONFIGURED.get_or_init(|| {
        serde_json::from_str::<Value>(include_str!("../tauri.conf.json"))
            .ok()
            .and_then(|config| {
                config
                    .pointer("/plugins/updater/pubkey")
                    .and_then(Value::as_str)
                    .map(|key| !key.trim().is_empty())
            })
            .unwrap_or(false)
    })
}
