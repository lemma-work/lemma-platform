import { useSyncExternalStore } from "react";
import { isLocalDeployment } from "@/site/config";

/** The one door from this page into the Lemma desktop shell.
 *
 *  The shell loads the workspace from a remote origin — the locald-served
 *  `app.lemma.localhost`, or the hosted site — and Tauri lets such an origin
 *  call only the commands `desktop/capabilities/workspace.json` grants it.
 *  Every call from this app goes through `invoke` below, and `invoke` accepts
 *  only the names in `WORKSPACE_COMMANDS`, so a command the shell would refuse
 *  cannot be written here without the type checker, and then
 *  `tests/desktop-ipc.test.ts`, saying so. Nothing else in `src` reads
 *  `__TAURI__`; the same test holds that too.
 *
 *  Two globals, two different questions. `__TAURI__` is the bridge: present
 *  means this page can talk to the shell. `__LEMMA_DESKTOP__` is what the shell
 *  says about itself — version, mode and platform — injected by
 *  `desktop/src/appearance.rs` before any page script runs, and never removed.
 */

/** What the shell says about itself.
 *
 *  `mode` is baked into the window's initialization script when the window is
 *  built, and choosing local or hosted afterwards does not rebuild it — so for
 *  the rest of that session it can still say what was true at launch. Read it
 *  only for the question it is reliable for (which sign-in the shell expects);
 *  whether the stack here is local comes from the deployment, which the
 *  frontend that stack serves cannot be older than. `platform` is
 *  `std::env::consts::OS` and cannot go stale. Optional, because locald serves a
 *  frontend pack that updates independently of the shell, and an older shell
 *  never injected it. */
export interface DesktopInfo {
    version: string;
    mode: "local" | "hosted" | "undecided";
    platform?: string;
}

type ShellInvoke = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

declare global {
    interface Window {
        __TAURI__?: { core?: { invoke?: ShellInvoke } };
        __LEMMA_DESKTOP__?: DesktopInfo;
    }
}

/** Every command this app may call, and no other.
 *
 *  Each must be granted to the workspace origin in
 *  `desktop/capabilities/workspace.json` and registered in `desktop/src/app.rs`;
 *  a name missing from either fails at runtime with an ACL error the user
 *  cannot act on. The IPC contract test reads both files. */
export const WORKSPACE_COMMANDS = [
    "agent_host_status",
    "agent_host_start",
    "agent_host_pair",
    "agent_host_session",
    "agent_host_refresh",
    "agent_host_open_log",
    /* Whether a coding agent here also loads its owner's own skills and
       settings. About the agents on this computer, so granted with the
       Agent Host commands, hosted workspaces included. */
    "agent_host_own_settings",
    /* Local settings, at a page. What "Check for updates" opens where
       Settings → This Mac does not exist: a hosted workspace in the app. */
    "open_control_center",
    "sandbox_image_status",
    "conversation_folder",
    "bind_conversation_folder",
    "unbind_conversation_folder",
    "adopt_conversation_folder",
    /* The address to frame a pod app at: an alias on the workspace's own
       host on macOS, the app's own URL elsewhere. See `pod-apps.ts`. */
    "app_frame_url",
    /* Settings → This Mac. Each also refuses in Rust unless the caller is
       this installation's own workspace on its loopback origin, so the hosted
       site and a shared origin reach none of them. */
    "local_settings_snapshot",
    "apply_local_settings",
    "local_sharing",
    "set_start_at_login",
    "set_host_execution",
    "repair_runtime",
    "open_logs",
    "prepare_sandbox_image",
    "check_for_app_update",
    "install_app_update",
    "telemetry_status",
    "set_telemetry_enabled",
    "diagnostic_logs",
    "discover_provider_models",
    "test_server_setup",
] as const;

export type WorkspaceCommand = (typeof WORKSPACE_COMMANDS)[number];

/** Whether this page is on an origin the shell grants its commands to.
 *
 *  On a local deployment that is the loopback workspace host alone. While the
 *  installation is shared, the app's own window moves to the LAN address or the
 *  tunnel host, where the shell still injects its globals but the capability
 *  grants nothing — so every call was refused, the automatic Agent Host
 *  connection failed on each page load, and the "This computer" card showed
 *  the error. There is no shell to talk to from there; saying so is the fix. */
export function onShellOrigin(): boolean {
    if (typeof window === "undefined" || !isLocalDeployment()) return true;
    const host = (window.location?.hostname ?? "").toLowerCase();
    return host === "localhost"
        || host === "127.0.0.1"
        || host.endsWith(".localhost");
}

function shellInvoke(): ShellInvoke | null {
    if (typeof window === "undefined") return null;
    const invoke = window.__TAURI__?.core?.invoke;
    return typeof invoke === "function" && onShellOrigin() ? invoke : null;
}

/** Whether this page is running inside the Lemma desktop app at all.
 *
 *  True for a hosted workspace in the app as much as a local one. Someone
 *  using a cloud workspace in the desktop app is in the desktop app; they
 *  simply have no local stack to command. */
export function isDesktop(): boolean {
    return shellInvoke() !== null;
}

/** What the shell said about itself, or null in a browser. */
export function desktopInfo(): DesktopInfo | null {
    if (typeof window === "undefined") return null;
    return window.__LEMMA_DESKTOP__ ?? null;
}

/** Whether the commands that act on *this installation* make sense here.
 *
 *  Local deployment and a reachable shell, both. A LAN or public-link browser
 *  is on a local deployment with no shell, and those origins are deliberately
 *  absent from the capability; a hosted workspace in the app has the shell and
 *  no local stack for it to command. */
export function desktopBridgeAvailable(): boolean {
    return isLocalDeployment() && isDesktop();
}

/** Call the shell. Throws in a browser, and whatever the shell threw.
 *
 *  The shell answers with loose JSON that its Rust side is free to change, so
 *  `T` is a claim the caller makes rather than one this checks: every caller
 *  here narrows the answer it gets before trusting its shape. */
export async function invoke<T = unknown>(command: WorkspaceCommand, args?: Record<string, unknown>): Promise<T> {
    const call = shellInvoke();
    if (!call) throw new Error("This can only be done in the Lemma desktop app.");
    return (await call(command, args)) as T;
}

/* The shell injects its globals before any page script and never removes them,
   so there is nothing to subscribe to. What `useSyncExternalStore` buys is a
   server snapshot React reconciles instead of keeping: read during render, the
   server — which has no `window` — answers "browser" into the HTML, and that
   is what someone sitting in the app would be shown. */
function subscribeNothing(): () => void {
    return () => {};
}

export function useIsDesktop(): boolean {
    return useSyncExternalStore(subscribeNothing, isDesktop, () => false);
}

export function useDesktopBridge(): boolean {
    return useSyncExternalStore(subscribeNothing, desktopBridgeAvailable, () => false);
}

/** How the workspace should put a pod app beside the agent.
 *
 *  - `direct`: frame the app's own URL. Browsers, WebView2 and a hosted
 *    workspace all treat the workspace and its apps as one site, so the frame
 *    is first-party and signed in.
 *  - `alias`: the macOS app on a local install. WebKit derives no site wider
 *    than the host from `*.localhost`, so `<slug>.apps.lemma.localhost` framed
 *    by `app.lemma.localhost` is third-party and gets no cookies -- measured,
 *    and no cookie attribute changes it. The same host on another port is
 *    same-site, so the shell hands back an alias on the workspace's own host
 *    (`app_frame_url`) and that is framed instead.
 *  - `window`: macOS on `*.localhost` without a shell that can alias -- one too
 *    old to say its platform, or reached from somewhere it will not answer.
 *    The app opens in its own window, top-level and signed in.
 *
 *  Derived from `platform` and the hostname, which never go stale. */
export type AppFrameMode = "direct" | "alias" | "window";

export function appFrameMode(): AppFrameMode {
    if (typeof window === "undefined") return "direct";
    const info = window.__LEMMA_DESKTOP__;
    if (!info) return "direct";
    if (info.platform && info.platform !== "macos") return "direct";
    if (!window.location.hostname.endsWith(".localhost")) return "direct";
    return info.platform === "macos" && desktopBridgeAvailable() ? "alias" : "window";
}

export function useAppFrameMode(): AppFrameMode {
    return useSyncExternalStore(subscribeNothing, appFrameMode, () => "direct");
}
