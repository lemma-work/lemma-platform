/** The app host's sign-in page: trade the API session for this host's app cookie. */
import { AuthManager, buildAuthUrl, dropUpdateMarker, isHalfCleared } from "./auth.js";
import { ApiError, HttpClient } from "./http.js";
import { HostRefusedError, askHostForAppAccess, isEmbeddedInHost } from "./embedded.js";
import Session from "supertokens-web-js/recipe/session/index.js";

export interface AppAccessOptions {
  apiUrl: string;
  authUrl: string;
  /** Where "Go to Lemma" leads when this account may not open the app. */
  homeUrl?: string;
}

interface AppAccessTicket {
  ticket: string;
  expires_in_seconds: number;
}

type AccessFailure = "signed-out" | "denied" | "unavailable";
type PageState = "checking" | AccessFailure | "blocked";

/** One heading and one sentence per state, and the action that state offers. */
const COPY: Record<PageState, { title: string; message: string }> = {
  checking: { title: "Opening this app", message: "Checking that it’s shared with you." },
  "signed-out": { title: "Sign in to open this app", message: "This app is private. Sign in with the Lemma account it’s shared with, and you’ll come straight back here." },
  denied: { title: "This app isn’t shared with you", message: "You’re signed in, but this account can’t open it. Ask the person who shared the link to give you access." },
  unavailable: { title: "We couldn’t check your access", message: "Something went wrong on our side. Try again in a moment." },
  blocked: { title: "Your browser blocked app access", message: "Allow cookies for this site, then try again." },
};

function failureKind(error: unknown): AccessFailure {
  if (error instanceof HostRefusedError) return "denied";
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
  // A host that kept the update marker from an ended session answers "no"
  // here without asking, though the person may have signed in since: opening
  // a private app the next morning, its cookie and the API token both lapsed,
  // sent a signed-in person to sign in. Only reached once the ticket request
  // was refused, so a good token never pays for the question.
  if (isHalfCleared()) dropUpdateMarker();
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

async function redeem(ticket: string, embedded: boolean): Promise<void> {
  // Framed in an AI tool, the server sets a cookie made for that frame: one a
  // browser sends from inside another site's page, kept to that page.
  const body = embedded ? { ticket, embedded: true } : { ticket };
  const response = await fetch("/_lemma/app-access/redeem", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(10_000),
  });
  // A refused ticket was issued seconds ago for this origin, so it is never
  // "signed out" -- only something to try again.
  if (!response.ok) throw new Error("App access could not be established");
}

/** Runs only in the server-owned sign-in page, before any private app code loads. */
export async function startAppAccess(options: AppAccessOptions): Promise<void> {
  const title = document.getElementById("app-access-title");
  const status = document.getElementById("app-access-status");
  const signIn = document.getElementById("app-access-sign-in") as HTMLAnchorElement | null;
  const retry = document.getElementById("app-access-retry") as HTMLButtonElement | null;
  const home = document.getElementById("app-access-home") as HTMLAnchorElement | null;
  const host = document.getElementById("app-access-host");
  if (!status || !signIn || !retry) return;
  // The browser knows the address it asked for; the server's page names none.
  if (host) host.textContent = window.location.host;
  const show = (state: PageState) => {
    document.body.dataset.state = state;
    if (title) title.textContent = COPY[state].title;
    status.textContent = COPY[state].message;
    retry.hidden = state !== "unavailable" && state !== "blocked";
    signIn.hidden = state !== "signed-out";
    if (home) {
      home.hidden = state !== "denied";
      if (options.homeUrl) home.href = options.homeUrl;
    }
    if (state === "signed-out") {
      signIn.href = signInUrlForApp(options.authUrl, window.location.href);
      // In a workspace tab, sign in at the top: the portal is not framed.
      signIn.target = window.parent === window ? "_self" : "_top";
      signIn.focus();
    }
  };
  retry.onclick = () => { void startAppAccess(options); };
  show("checking");
  try {
    // Framed in an AI tool there is no Lemma session to read a ticket with;
    // the Lemma view around the frame mints one from the tool's connection.
    const embedded = isEmbeddedInHost();
    const ticket = embedded ? await askHostForAppAccess() : (await requestTicket(options)).ticket;
    await redeem(ticket, embedded);
    // HttpOnly cookies cannot be read by JavaScript. Ask the server before
    // reloading, so a browser that refuses the cookie cannot enter a loop.
    const verified = await fetch("/", { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/octet-stream" }, signal: AbortSignal.timeout(10_000) });
    if (verified.status === 401) { show("blocked"); return; }
    if (!verified.ok) { show("unavailable"); return; }
    // Replacing the same URL with a fragment can be a same-document navigation.
    // Reload only after the server has confirmed the cookie was accepted.
    window.location.reload();
  } catch (error) {
    show(failureKind(error));
  }
}
