// Declaring an app manifest turns Tauri's ACL on for this app's own commands,
// not just plugin commands. Every command below must then be granted to a
// specific webview by a capability in `capabilities/`, and anything not granted
// is rejected - including from the bundled pages. That is the point: the
// workspace runs on a remote origin (the locald-served app URL, or the hosted
// site), and a remote origin can only reach a command through a capability that
// names its URL. Without a manifest there are no `allow-*` permissions to name,
// so the workspace cannot be granted anything at all.
const COMMANDS: &[&str] = &[
    "start",
    "stop",
    "restart",
    "open_app",
    "open_logs",
    "diagnostic_logs",
    "choose_connection_mode",
    "set_connection_mode",
    "get_state",
    "login",
    "open_control_center",
    "prepare_runtime",
    "runtime_info",
    "repair_runtime",
    "control_snapshot",
    "agent_host_action",
    "agent_host_status",
    "sandbox_image_status",
    "prepare_sandbox_image",
    "agent_host_start",
    "agent_host_pair",
    "agent_host_refresh",
    "agent_host_open_log",
    "apply_operator_config",
    "discover_provider_models",
    "configure_ai_provider",
    "sharing_action",
    "close_local_settings",
    "confirm_destructive_action",
    "confirm_settings_changes",
    "resolve_confirmation",
    "open_developer_tools",
    "local_recovery_options",
    "reset_local_data",
    "reset_full_reinstall",
    "restart_into_recovery",
    "check_for_app_update",
    "install_app_update",
    "telemetry_status",
    "set_telemetry_enabled",
];

fn main() {
    // `main.rs` reads these with `option_env!`, which is resolved at compile
    // time -- and cargo does not rebuild a crate because an environment
    // variable changed. Both release jobs restore a warm cache, so without
    // these a build that first sets the channel would reuse an object file
    // compiled without it and ship a release that reports itself as `dev` and
    // refuses to self-update.
    println!("cargo:rerun-if-env-changed=LEMMA_RELEASE_CHANNEL");
    println!("cargo:rerun-if-env-changed=LEMMA_BUILD_SHA");
    // Which directory this build keeps its data in, when it must not be the
    // one the user's installed Lemma is using.
    //
    // Qualifying a candidate means running it on the same Mac as the real
    // installation, and the two sharing `Application Support/Lemma` would let
    // a test build stop the user's daemon, adopt its runtime, and reset its
    // data. Baked at build time rather than passed at launch so the isolation
    // is a property of the artifact: a candidate that is handed to somebody,
    // or double-clicked from the Finder, stays isolated with no environment to
    // remember.
    println!("cargo:rerun-if-env-changed=LEMMA_DESKTOP_DATA_DIR_NAME");
    // Windows stops at 260 characters unless a binary says otherwise, and the
    // paths this app builds are long by construction: an installation prefix,
    // then a release directory named by version and artifact identity, then a
    // Python site-packages tree inside it. `build_local_host_pack.py` already
    // budgets for MAX_PATH and reports how little headroom is left; this is the
    // other half, and the two are independent -- the budget keeps the pack
    // installable on a machine that has not opted in, and this lets a machine
    // that has opted in stop being the constraint.
    //
    // Tauri's own default manifest is the Common-Controls dependency and
    // nothing else, so this is that manifest plus one setting rather than a
    // replacement that drops something.
    let windows = tauri_build::WindowsAttributes::new().app_manifest(
        r#"<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <dependency>
    <dependentAssembly>
      <assemblyIdentity
        type="win32"
        name="Microsoft.Windows.Common-Controls"
        version="6.0.0.0"
        processorArchitecture="*"
        publicKeyToken="6595b64144ccf1df"
        language="*"
      />
    </dependentAssembly>
  </dependency>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
    </windowsSettings>
  </application>
</assembly>
"#,
    );
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(COMMANDS))
            .windows_attributes(windows),
    )
    .expect("failed to run tauri-build");
}
