/**
 * The globals the desktop shell injects into the workspace webview.
 *
 * These live here rather than beside any one consumer. `__TAURI__` was
 * previously declared inside `components/desktop/local-settings-button.tsx`
 * while four modules under `lib/desktop/` depended on it, so moving or
 * deleting one button broke typechecking in files that never referenced it.
 *
 * `invoke` is deliberately typed as returning `unknown`: the shell answers with
 * loose JSON, and every caller narrows it (see `readStatus` in
 * `agent-host-bridge.ts`) rather than asserting a shape the Rust side is free
 * to change.
 */
declare global {
  interface Window {
    __TAURI__?: {
      core?: {
        invoke?: (command: string, args?: Record<string, unknown>) => Promise<unknown>;
      };
    };
  }
}

export {};
