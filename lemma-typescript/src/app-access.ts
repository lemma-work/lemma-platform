/** The app host's sign-in page: trade the API session for this host's app cookie. */
import { AuthManager, buildAuthUrl } from "./auth.js";
import { ApiError, HttpClient } from "./http.js";
import Session from "supertokens-web-js/recipe/session/index.js";

export interface AppAccessOptions {
  apiUrl: string;
  authUrl: string;
}

interface AppAccessTicket {
  ticket: string;
  expires_in_seconds: number;
}

type AccessFailure = "signed-out" | "denied" | "unavailable";

function failureKind(error: unknown): AccessFailure {
  if (error instanceof ApiError && error.statusCode === 401) return "signed-out";
  if (error instanceof ApiError && [403, 404].includes(error.statusCode)) return "denied";
  return "unavailable";
}

function signInUrlForApp(authUrl: string, redirectUri: string): string {
  const url = new URL(authUrl);
  // Hosted settings may name the portal's origin. Its sign-in screen lives
  // under /auth; an explicit configured portal path is already complete.
  if (url.pathname === "/") url.pathname = "/auth";
  return buildAuthUrl(url.href, { redirectUri });
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

/** A ticket for this page's own origin, read with the API's session cookie. */
async function requestTicket(options: AppAccessOptions): Promise<AppAccessTicket> {
  const http = new HttpClient(options.apiUrl, new AuthManager(options.apiUrl, options.authUrl), { timeoutMs: 10_000, maxRetries: 0 });
  // The API names the app from this request's Origin, so there is no body.
  const send = () => http.request<AppAccessTicket>("POST", "/apps/access/tickets?superTokensDoNotDoInterception=true", { headers: { rid: "session" } });
  try {
    return await send();
  } catch (error) {
    // An expired access token, with a refresh token still good, is a 401 too.
    if (!(error instanceof ApiError) || error.statusCode !== 401 || !(await refreshMainSession())) throw error;
    return await send();
  }
}

async function redeem(ticket: string): Promise<void> {
  const response = await fetch("/_lemma/app-access/redeem", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ticket }),
    credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(10_000),
  });
  // A refused ticket was issued seconds ago for this origin, so it is never
  // "signed out" -- only something to try again.
  if (!response.ok) throw new Error("App access could not be established");
}

/** Runs only in the server-owned sign-in page, before any private app code loads. */
export async function startAppAccess(options: AppAccessOptions): Promise<void> {
  const status = document.getElementById("app-access-status");
  const signIn = document.getElementById("app-access-sign-in") as HTMLAnchorElement | null;
  const retry = document.getElementById("app-access-retry") as HTMLButtonElement | null;
  if (!status || !signIn || !retry) return;
  retry.onclick = () => { void startAppAccess(options); };
  retry.hidden = true;
  signIn.hidden = true;
  status.textContent = "Checking your access…";
  const showFailure = (kind: AccessFailure) => {
    status.textContent = kind === "signed-out" ? "Sign in to open this app." : kind === "denied" ? "This app isn’t available to your account." : "We couldn’t check your access. Try again.";
    retry.hidden = kind === "signed-out";
    if (kind === "signed-out") {
      signIn.href = signInUrlForApp(options.authUrl, window.location.href);
      // In a workspace tab, sign in at the top: the portal is not framed.
      signIn.target = window.parent === window ? "_self" : "_top";
      signIn.hidden = false;
    }
  };
  try {
    const { ticket } = await requestTicket(options);
    await redeem(ticket);
    // HttpOnly cookies cannot be read by JavaScript. Ask the server before
    // reloading, so a browser that refuses the cookie cannot enter a loop.
    const verified = await fetch("/", { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/octet-stream" }, signal: AbortSignal.timeout(10_000) });
    if (verified.status === 401) {
      status.textContent = "Your browser blocked app access. Allow cookies for this site, then try again.";
      retry.hidden = false;
      return;
    }
    if (!verified.ok) { showFailure("unavailable"); return; }
    // Replacing the same URL with a fragment can be a same-document navigation.
    // Reload only after the server has confirmed the cookie was accepted.
    window.location.reload();
  } catch (error) {
    showFailure(failureKind(error));
  }
}
