/** The trusted host bootstrap exchanges a one-use code for an asset cookie. */
import { AuthManager, buildAuthUrl } from "./auth.js";
import { ApiError, HttpClient } from "./http.js";
import Session from "supertokens-web-js/recipe/session/index.js";
import type { AppAccessAuthorizeResponse } from "./openapi_client/models/AppAccessAuthorizeResponse.js";

export interface AppAccessOptions {
  apiUrl: string;
  authUrl: string;
}

export interface AppAccessFrameOptions extends AppAccessOptions {
  appOrigin: string;
}

export interface AppAccessBootstrapOptions extends AppAccessOptions {
  parentOrigin: string;
}

type AppAccessAuthorization = AppAccessAuthorizeResponse;

type AccessFailure = "signed-out" | "denied" | "unavailable";
const REQUEST_MESSAGE = "lemma:app-access:request";
const RESULT_MESSAGE = "lemma:app-access:result";
const OPAQUE_VALUE = /^[A-Za-z0-9_-]{43}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function failureKind(error: unknown): AccessFailure {
  if (error instanceof ApiError && error.code === "APP_ACCESS_INVALID") return "unavailable";
  if (error instanceof ApiError && error.statusCode === 401) return "signed-out";
  if (error instanceof ApiError && [403, 404, 410].includes(error.statusCode)) return "denied";
  return "unavailable";
}

function accessTransport(options: AppAccessOptions): HttpClient {
  return new HttpClient(options.apiUrl, new AuthManager(options.apiUrl, options.authUrl), { timeoutMs: 10_000, maxRetries: 0 });
}

async function refreshMainSession(): Promise<boolean> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error("The session service did not answer")), 10_000);
  });
  try {
    return await Promise.race([Session.attemptRefreshingSession(), deadline]);
  } finally {
    clearTimeout(timer);
  }
}

async function authorize(http: HttpClient, requestId: string, appOrigin: string, signal?: AbortSignal): Promise<AppAccessAuthorization> {
  // A new app origin has no SDK marker yet. Ask the server before refreshing:
  // the API's host-only cookie can already authenticate this request.
  const send = () => http.request<AppAccessAuthorization>("POST", `/apps/access/requests/${requestId}/authorize?superTokensDoNotDoInterception=true`, {
    body: { app_origin: appOrigin }, headers: { rid: "session" }, signal,
  });
  let result: AppAccessAuthorization;
  try {
    result = await send();
  } catch (error) {
    if (!(error instanceof ApiError) || error.statusCode !== 401 || error.code === "APP_ACCESS_INVALID" || !(await refreshMainSession())) throw error;
    result = await send();
  }
  if (!OPAQUE_VALUE.test(result.code)) throw new Error("Invalid app access response");
  return result;
}

/** Only a known frame on its exact app origin may request an asset handoff. */
export function registerAppAccessFrame(frame: HTMLIFrameElement, options: AppAccessFrameOptions): () => void {
  const appOrigin = new URL(options.appOrigin).origin;
  const http = accessTransport(options);
  const abort = new AbortController();
  const pending = new Set<string>();
  let inFlight = 0;
  let active = true;
  const listener = (event: MessageEvent<unknown>) => {
    if (event.source !== frame.contentWindow || event.origin !== appOrigin || !isRecord(event.data)) return;
    const data = event.data;
    if (data.type !== REQUEST_MESSAGE || typeof data.requestId !== "string" || !OPAQUE_VALUE.test(data.requestId) || pending.has(data.requestId) || inFlight >= 4) return;
    const requestId = data.requestId;
    pending.add(requestId);
    if (pending.size > 64) pending.delete(pending.values().next().value!);
    inFlight++;
    const respond = (result: Record<string, string>) => {
      if (active) frame.contentWindow?.postMessage({ type: RESULT_MESSAGE, requestId, ...result }, appOrigin);
    };
    void authorize(http, requestId, appOrigin, abort.signal).then(
      result => respond({ code: result.code }),
      error => respond({ error: failureKind(error), signInUrl: buildAuthUrl(options.authUrl, { redirectUri: window.location.href }) }),
    ).finally(() => { inFlight--; });
  };
  window.addEventListener("message", listener);
  return () => {
    active = false;
    abort.abort();
    window.removeEventListener("message", listener);
  };
}

function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function localRequest<T>(path: string, body: Record<string, string>): Promise<T> {
  const response = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new ApiError(response.status, "App access could not be established");
  return response.json() as Promise<T>;
}

function authorizeThroughParent(requestId: string, parentOrigin: string): Promise<AppAccessAuthorization | { error: AccessFailure; signInUrl?: string }> {
  return new Promise((resolve, reject) => {
    const stop = () => { clearTimeout(timer); window.removeEventListener("message", listener); };
    const listener = (event: MessageEvent<unknown>) => {
      if (event.source !== window.parent || event.origin !== parentOrigin || !isRecord(event.data)) return;
      const data = event.data;
      if (data.type !== RESULT_MESSAGE || data.requestId !== requestId) return;
      if (typeof data.code === "string" && OPAQUE_VALUE.test(data.code)) {
        stop(); resolve({ code: data.code, expires_in_seconds: 60 });
      } else if (["signed-out", "denied", "unavailable"].includes(String(data.error))) {
        stop(); resolve({ error: data.error as AccessFailure, signInUrl: typeof data.signInUrl === "string" ? data.signInUrl : undefined });
      }
    };
    const timer = setTimeout(() => { stop(); reject(new Error("The workspace did not answer")); }, 10_000);
    window.addEventListener("message", listener);
    window.parent.postMessage({ type: REQUEST_MESSAGE, requestId }, parentOrigin);
  });
}

/** Runs only in the server-owned gate page, before any private app code loads. */
export async function startAppAccess(options: AppAccessBootstrapOptions): Promise<void> {
  const status = document.getElementById("app-access-status");
  const signIn = document.getElementById("app-access-sign-in") as HTMLAnchorElement | null;
  const retry = document.getElementById("app-access-retry") as HTMLButtonElement | null;
  if (!status || !signIn || !retry) return;
  retry.onclick = () => { void startAppAccess(options); };
  retry.hidden = true;
  signIn.hidden = true;
  status.textContent = "Checking your access…";
  const showFailure = (kind: AccessFailure, signInUrl?: string) => {
    status.textContent = kind === "signed-out" ? "Sign in to open this app." : kind === "denied" ? "This app isn’t available to your account." : "We couldn’t check your access. Try again.";
    retry.hidden = kind === "signed-out";
    if (kind === "signed-out") {
      // Parent replies are accepted only from the configured workspace, and
      // the link must still belong to the configured authentication service.
      const candidate = signInUrl && new URL(signInUrl, options.authUrl);
      signIn.href = candidate && candidate.origin === new URL(options.authUrl).origin ? candidate.href : buildAuthUrl(options.authUrl, { redirectUri: window.location.href });
      signIn.target = window.parent === window ? "_self" : "_top";
      signIn.hidden = false;
    }
  };
  try {
    const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)));
    const challenge = base64url(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
    const started = await localRequest<{ request_id: string }>("/_lemma/app-access/requests", { challenge });
    if (!OPAQUE_VALUE.test(started.request_id)) throw new Error("Invalid handoff request");
    const authorized = window.parent === window
      ? await authorize(accessTransport(options), started.request_id, window.location.origin)
      : await authorizeThroughParent(started.request_id, options.parentOrigin);
    if ("error" in authorized) { showFailure(authorized.error, authorized.signInUrl); return; }
    await localRequest("/_lemma/app-access/redeem", { request_id: started.request_id, code: authorized.code, verifier });
    // HttpOnly cookies cannot be read by JavaScript. Verify with the server
    // before reloading, so a browser blocking storage cannot enter a loop.
    const verified = await fetch("/", { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/octet-stream" }, signal: AbortSignal.timeout(10_000) });
    if (verified.status === 401) {
      status.textContent = "Your browser blocked app access. Allow cookies for this site, then try again.";
      retry.hidden = false;
      return;
    }
    if (!verified.ok) { showFailure(verified.status === 404 ? "denied" : "unavailable"); return; }
    // Replacing the same URL with a fragment can be a same-document navigation.
    // Reload only after the server has confirmed the cookie was accepted.
    window.location.reload();
  } catch (error) {
    showFailure(failureKind(error));
  }
}
