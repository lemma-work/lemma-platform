import type { KnownClient } from "./version";

export interface LemmaAppConfig {
  name?: string;
  description?: string;
  iconUrl?: string;
}

export interface LemmaConfig {
  /** API base URL, e.g. https://api.lemma.work */
  apiUrl: string;
  /** Auth service URL, e.g. https://lemma.work/auth */
  authUrl: string;
  /** Pod ID to scope all pod-level API calls */
  podId?: string;
  /**
   * A credential to send as `Authorization: Bearer`.
   *
   * The supported way to authenticate outside a browser — a Node script, a
   * Lambda, an MCP server — where there is no session cookie to rely on. In a
   * browser leave it unset: the cookie flow handles anti-CSRF and refresh, and
   * a token pasted into page config is a token in the page.
   */
  token?: string;
  app?: LemmaAppConfig;
  /** Per-request timeout in ms (default 30000). */
  timeoutMs?: number;
  /** Max automatic retries on 429/502/503/504 (default 2). */
  maxRetries?: number;
  /** Which Lemma client this is, sent as `X-Lemma-Client` and resolved to an
   *  origin by the backend. Leave unset in a third-party integration: an
   *  unnamed caller is `SDK`, which is the truthful answer. The web app and
   *  Desktop set it so a person in a browser is not counted as a script. */
  client?: KnownClient;
  /** Which app this page is, injected by the host at serve time. Sent as
   *  `X-Lemma-App` so a published app's API calls are attributable to the app
   *  rather than only to the pod. */
  appId?: string;
}

declare global {
  interface Window {
    __LEMMA_CONFIG__?: Partial<LemmaConfig>;
  }
}

/** The public API, used when nothing else names a host. */
export const DEFAULT_API_URL = "https://api.lemma.work";
/** The public auth service, used when nothing else names one. */
export const DEFAULT_AUTH_URL = "https://lemma.work/auth";

function fromEnv(key: string): string | undefined {
  // Vite: import.meta.env.VITE_*
  // CRA / webpack: process.env.REACT_APP_*
  // Node: process.env.*
  //
  // Both sources are consulted, not just the first one that exists: under Vite
  // SSR and under a test runner `import.meta.env` is present but carries only
  // the build's own VITE_ keys, so a plain shell export lands in `process.env`
  // and would otherwise be invisible.
  try {
    // @ts-ignore — import.meta is valid in ESM/Vite builds; try/catch guards CJS bundles
    const meta = (import.meta as { env?: Record<string, string | undefined> }).env; // eslint-disable-line
    if (meta) {
      const value =
        meta[`VITE_LEMMA_${key}`] ??
        meta[`REACT_APP_LEMMA_${key}`] ??
        meta[`LEMMA_${key}`];
      if (value) {
        return value;
      }
    }
  } catch {
    // not available in CJS/browser bundle context
  }
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const env = (globalThis as any).process?.env;
    if (env) {
      return env[`LEMMA_${key}`] || undefined;
    }
  } catch {
    // not available
  }
  return undefined;
}

/** Warn once per name, so a misconfigured process says so instead of quietly
 *  talking to the wrong host for the rest of its life. */
const warned = new Set<string>();

function warnOnce(key: string, message: string): void {
  if (warned.has(key)) {
    return;
  }
  warned.add(key);
  // eslint-disable-next-line no-console
  console.warn(`lemma-sdk: ${message}`);
}

/** Test seam: the warnings above fire once per process by design. */
export function resetConfigWarnings(): void {
  warned.clear();
}

/**
 * The API base URL from the environment.
 *
 * `LEMMA_BASE_URL` is the name the Python SDK, the CLI and every Lemma doc use,
 * so it is the one this reads. `LEMMA_API_URL` was this SDK's own name and is
 * still accepted, with a warning: a script that set the documented name used to
 * fall through to the production default and reach the wrong host in silence.
 */
function apiUrlFromEnv(): string | undefined {
  const baseUrl = fromEnv("BASE_URL");
  if (baseUrl) {
    return baseUrl;
  }
  const legacy = fromEnv("API_URL");
  if (legacy) {
    warnOnce(
      "API_URL",
      "LEMMA_API_URL is deprecated; rename it to LEMMA_BASE_URL, the name the CLI and the Python SDK use.",
    );
  }
  return legacy;
}

function windowConfig(): Partial<LemmaConfig> {
  if (typeof window !== "undefined" && window.__LEMMA_CONFIG__) {
    return window.__LEMMA_CONFIG__;
  }
  return {};
}

/**
 * Resolve the config a client will use.
 *
 * Order, highest first: explicit `overrides` → `window.__LEMMA_CONFIG__` (set by
 * the host that serves a pod app) → environment (`LEMMA_BASE_URL`, and the
 * `VITE_`/`REACT_APP_` prefixed forms of it) → the public defaults.
 */
export function resolveConfig(overrides: Partial<LemmaConfig> = {}): LemmaConfig {
  const win = windowConfig();

  const configuredApiUrl = overrides.apiUrl ?? win.apiUrl ?? apiUrlFromEnv();
  if (!configuredApiUrl && typeof window === "undefined") {
    // In a browser the default is the answer: the page is served by Lemma. A
    // server-side caller with nothing configured is usually a script that meant
    // to point somewhere else, so name the host rather than just using it.
    warnOnce(
      "BASE_URL",
      `no API URL configured, using ${DEFAULT_API_URL}. Set LEMMA_BASE_URL or pass apiUrl to point elsewhere.`,
    );
  }
  const apiUrl = configuredApiUrl ?? DEFAULT_API_URL;

  const authUrl =
    overrides.authUrl ?? win.authUrl ?? fromEnv("AUTH_URL") ?? DEFAULT_AUTH_URL;

  const podId =
    overrides.podId ??
    win.podId ??
    fromEnv("POD_ID");

  // `LEMMA_TOKEN` as well as the explicit field: a server-side caller usually
  // has the credential in the environment already, and reading it here means
  // the common case needs no code at all. Deliberately not read from
  // `windowConfig()` — a token in page config is a token in the page.
  const token = overrides.token ?? fromEnv("TOKEN");

  return {
    apiUrl: apiUrl.replace(/\/$/, ""),
    authUrl: authUrl.replace(/\/$/, ""),
    podId,
    token,
    app: overrides.app ?? win.app,
    timeoutMs: overrides.timeoutMs ?? win.timeoutMs,
    maxRetries: overrides.maxRetries ?? win.maxRetries,
    client: overrides.client ?? win.client,
    appId: overrides.appId ?? win.appId,
  };
}
