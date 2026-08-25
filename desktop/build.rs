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
    "installer_log",
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
    "open_developer_tools",
    "local_recovery_options",
    "reset_local_data",
    "reset_full_reinstall",
];

fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(COMMANDS)),
    )
    .expect("failed to run tauri-build");
}
