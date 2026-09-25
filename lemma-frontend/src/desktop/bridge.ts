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
    "agent_host_refresh",
    "agent_host_open_log",
    "sandbox_image_status",
    "conversation_folder",
    "bind_conversation_folder",
    "unbind_conversation_folder",
    "adopt_conversation_folder",
    /* Settings → This Mac. Each also refuses in Rust unless the caller is
       this installation's own workspace on its loopback origin, so the hosted
       site and a shared origin reach none of them. */
    "local_settings_snapshot",
    "apply_local_settings",
    "local_sharing",
    "set_start_at_login",
    "repair_runtime",
    "open_logs",
    "prepare_sandbox_image",
    "check_for_app_update",
    "install_app_update",
    "telemetry_status",
    "set_telemetry_enabled",
    "diagnostic_logs",
    "discover_provider_models",
] as const;

export type WorkspaceCommand = (typeof WORKSPACE_COMMANDS)[number];

function shellInvoke(): ShellInvoke | null {
    if (typeof window === "undefined") return null;
    const invoke = window.__TAURI__?.core?.invoke;
    return typeof invoke === "function" ? invoke : null;
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

/** Whether an app embedded in an iframe would still be signed in.
 *
 *  On macOS it is not, and no cookie attribute changes that. `localhost` is
 *  not in the Public Suffix List, so WebKit derives no registrable domain and
 *  treats every `*.lemma.localhost` host as its own site: an app framed from
 *  `<slug>.apps.lemma.localhost` into `app.lemma.localhost` is third-party, its
 *  storage is blocked outright, and it loads permanently signed out while its
 *  SDK refreshes for ever. The same host at top level gets the session, which
 *  is why the answer is a window rather than a redesign.
 *
 *  Derived, not configured, from two things that never go stale: `platform`
 *  and the hostname. It corrects itself the moment local hostnames move to a
 *  real registrable domain. Chromium and WebView2 treat `*.localhost` as
 *  same-site, so browsers and the Windows build keep their iframes. */
export function crossSiteFramesCarryCookies(): boolean {
    if (typeof window === "undefined") return true;
    const info = window.__LEMMA_DESKTOP__;
    if (!info) return true;
    if (info.platform && info.platform !== "macos") return true;
    /* macOS, or a shell too old to say. Assuming the permissive case there
       brings back a signed-out iframe that retries for ever; the restrictive
       one costs a window. */
    return !window.location.hostname.endsWith(".localhost");
}

export function useCrossSiteFramesCarryCookies(): boolean {
    return useSyncExternalStore(subscribeNothing, crossSiteFramesCarryCookies, () => true);
}
