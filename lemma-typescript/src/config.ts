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
  app?: LemmaAppConfig;
  /** Per-request timeout in ms (default 30000). */
  timeoutMs?: number;
  /** Max automatic retries on 429/502/503/504 (default 2). */
  maxRetries?: number;
}

declare global {
  interface Window {
    __LEMMA_CONFIG__?: Partial<LemmaConfig>;
  }
}

function fromEnv(key: string): string | undefined {
  // Vite: import.meta.env.VITE_*
  // CRA / webpack: process.env.REACT_APP_*
  // Node: process.env.*
  try {
    // @ts-ignore — import.meta is valid in ESM/Vite builds; try/catch guards CJS bundles
    const meta = (import.meta as { env?: Record<string, string | undefined> }).env; // eslint-disable-line
    if (meta) {
      return (
        meta[`VITE_LEMMA_${key}`] ??
        meta[`REACT_APP_LEMMA_${key}`] ??
        meta[`LEMMA_${key}`]
      );
    }
  } catch {
    // not available in CJS/browser bundle context
  }
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const env = (globalThis as any).process?.env;
    if (env) {
      return env[`LEMMA_${key}`];
    }
  } catch {
    // not available
  }
  return undefined;
}

function windowConfig(): Partial<LemmaConfig> {
  if (typeof window !== "undefined" && window.__LEMMA_CONFIG__) {
    return window.__LEMMA_CONFIG__;
  }
  return {};
}

export function resolveConfig(overrides: Partial<LemmaConfig> = {}): LemmaConfig {
  const win = windowConfig();

  const apiUrl =
    overrides.apiUrl ??
    win.apiUrl ??
    fromEnv("API_URL") ??
    "https://api.lemma.work";

  const authUrl =
    overrides.authUrl ??
    win.authUrl ??
    fromEnv("AUTH_URL") ??
    "https://lemma.work/auth";

  const podId =
    overrides.podId ??
    win.podId ??
    fromEnv("POD_ID");

  return {
    apiUrl: apiUrl.replace(/\/$/, ""),
    authUrl: authUrl.replace(/\/$/, ""),
    podId,
    app: overrides.app ?? win.app,
    timeoutMs: overrides.timeoutMs ?? win.timeoutMs,
    maxRetries: overrides.maxRetries ?? win.maxRetries,
  };
}
