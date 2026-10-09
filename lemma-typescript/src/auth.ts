/**
 * Auth module — cookie-based auth (production) with Bearer token fallback
 * for agent/dev testing.
 *
 * Auth resolution order on init:
 * 1. localStorage.getItem("lemma_token")
 * 2. Session cookie (credentials: "include") — production path
 *
 * If a token is found in (1), all requests use Authorization: Bearer <token>.
 * Otherwise requests rely on cookies, and the server must set the session cookie
 * after the user authenticates at the auth service. In cookie mode we initialise
 * the SuperTokens browser SDK so fetch/XHR automatically handles anti-CSRF and
 * refresh-token flows for mutating requests.
 *
 * Auth state is determined by calling GET /users/me (user.current.get).
 * 401 → unauthenticated. 200 → authenticated.
 */

import Session from "supertokens-web-js/recipe/session/index.js";
import { ensureCookieSessionSupport } from "./supertokens.js";
import { EmbeddedCredentials, isEmbeddedInHost } from "./embedded.js";
import { isUnreachableStatus, probeReachable, refreshFailureKind } from "./reachability.js";

export interface UserInfo {
  id: string;
  email: string;
  name?: string;
  [key: string]: unknown;
}

/**
 * `unreachable` is the API not answering -- a network failure, or a 5xx from a
 * server that is restarting -- which says nothing about the session. Only a
 * 401 is `unauthenticated`, and only that should send anyone to sign in.
 */
export type AuthStatus = "loading" | "authenticated" | "unauthenticated" | "unreachable";

export interface AuthState {
  status: AuthStatus;
  user: UserInfo | null;
}

export type AuthListener = (state: AuthState) => void;

export type AuthRedirectMode = "login" | "signup";

type AuthQueryParams = Record<
  string,
  string | number | boolean | Array<string | number | boolean> | null | undefined
>;

export interface BuildAuthUrlOptions {
  /** Optional auth path segment relative to authUrl pathname, e.g. "callback" -> /auth/callback. */
  path?: string;
  /** Adds signup mode query, preserving existing params. */
  mode?: AuthRedirectMode;
  /** Redirect URI passed to auth service. */
  redirectUri?: string;
  /** Additional query parameters appended to auth URL. */
  params?: AuthQueryParams;
}

export interface BuildFederatedLogoutUrlOptions {
  /**
   * Optional auth path segment for logout, relative to authUrl pathname.
   * Defaults to "logout" (for example: https://auth.example.com/auth/logout).
   */
  path?: string;
  /**
   * Post-logout redirect URI passed to the auth service.
   */
  redirectUri?: string;
  /**
   * Query parameter name used for redirect URI. Defaults to "redirect_uri".
   */
  redirectParam?: string;
  /** Additional query parameters appended to logout URL. */
  params?: AuthQueryParams;
}

export interface RedirectToFederatedLogoutOptions
  extends Omit<BuildFederatedLogoutUrlOptions, "redirectUri"> {
  /**
   * Post-logout redirect URI. Defaults to current location.
   */
  redirectUri?: string;
  /**
   * Whether to clear the local session before redirecting upstream.
   * Defaults to true.
   */
  localSignOut?: boolean;
}

export interface ResolveSafeRedirectUriOptions {
  /** Origin for resolving relative paths. */
  siteOrigin: string;
  /** Fallback path or URL when input is empty/invalid/blocked. Defaults to "/". */
  fallback?: string;
  /** Local paths blocked as redirect targets to avoid auth loops. */
  blockedPaths?: string[];
  /** Additional exact origins that may receive redirects. */
  allowedOrigins?: string[];
  /** Hostname suffixes that may receive redirects, such as "apps.example.com". */
  allowedOriginSuffixes?: string[];
  /** Allow loopback origins for explicit local callbacks such as CLI login. */
  allowLoopback?: boolean;
}

const DEFAULT_BLOCKED_REDIRECT_PATHS = ["/login", "/signup", "/auth"];
const SUPERTOKENS_FRONTEND_MARKER_KEYS = [
  "sFrontToken",
  "st-last-access-token-update",
  "sIRTFrontend",
  "sAntiCsrf",
  "st-access-token",
  "st-refresh-token",
];

const LOCALSTORAGE_TOKEN_KEY = "lemma_token";

/**
 * Where a token lives when there is no `localStorage` to put it in.
 *
 * Outside a browser every one of the helpers below used to return early, so
 * `setTestingToken(...)` was a silent no-op: the call succeeded, no token was
 * stored, `AuthManager` started with none, and every request came back 401.
 * The package declares itself Node-loadable — `"main": "dist/index.js"`, no
 * `browser` condition — so "there is no window" cannot mean "there is no way
 * to authenticate".
 *
 * Module-scoped rather than global: a server-side caller who wants no shared
 * state passes `token` on `LemmaConfig` instead and never touches this.
 */
let memoryToken: string | null = null;

function readStorageToken(): string | null {
  if (typeof window === "undefined") return memoryToken;
  try {
    return localStorage.getItem(LOCALSTORAGE_TOKEN_KEY);
  } catch {
    return null;
  }
}

function writeStorageToken(token: string): void {
  if (typeof window === "undefined") {
    memoryToken = token;
    return;
  }
  try {
    localStorage.setItem(LOCALSTORAGE_TOKEN_KEY, token);
  } catch {
    // ignore storage errors
  }
}

function removeStorageToken(): void {
  if (typeof window === "undefined") {
    memoryToken = null;
    return;
  }
  try {
    localStorage.removeItem(LOCALSTORAGE_TOKEN_KEY);
  } catch {
    // ignore storage errors
  }
}

export function setTestingToken(token: string): void {
  writeStorageToken(token);
}

export function getTestingToken(): string | null {
  return readStorageToken();
}

export function clearTestingToken(): void {
  removeStorageToken();
}

function detectInjectedToken(): string | null {
  // `readStorageToken` answers from `localStorage` in a browser and from the
  // module-scoped fallback outside one, so this works in both.
  return readStorageToken();
}

function normalizePath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) return "/";
  if (trimmed === "/") return "/";
  const withLeadingSlash = trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
  return withLeadingSlash.endsWith("/") ? withLeadingSlash.slice(0, -1) : withLeadingSlash;
}

function resolveAuthPath(basePath: string, path?: string): string {
  const normalizedBase = normalizePath(basePath);
  if (!path || !path.trim()) {
    return normalizedBase;
  }
  const segment = path.trim().replace(/^\/+/, "");
  if (!segment) {
    return normalizedBase;
  }
  return `${normalizedBase}/${segment}`.replace(/\/{2,}/g, "/");
}

function isBlockedLocalPath(pathname: string, blockedPaths: string[]): boolean {
  const normalizedPathname = normalizePath(pathname);
  return blockedPaths.some((rawBlockedPath) => {
    const blockedPath = normalizePath(rawBlockedPath);
    return normalizedPathname === blockedPath || normalizedPathname.startsWith(`${blockedPath}/`);
  });
}

function normalizeOrigin(rawOrigin: string): string {
  const parsed = new URL(rawOrigin);
  return parsed.origin;
}

function normalizeHostnameSuffix(rawSuffix: string): string {
  return rawSuffix.trim().replace(/^\.+/, "").toLowerCase();
}

function isLoopbackHost(hostname: string): boolean {
  const normalized = hostname.toLowerCase().replace(/^\[/, "").replace(/\]$/, "");
  return normalized === "localhost" || normalized === "127.0.0.1" || normalized === "::1";
}

function isAllowedRedirectOrigin(
  parsed: URL,
  siteOrigin: string,
  options: ResolveSafeRedirectUriOptions,
): boolean {
  if (parsed.origin === siteOrigin) {
    return true;
  }

  if (options.allowLoopback && isLoopbackHost(parsed.hostname)) {
    return true;
  }

  for (const allowedOrigin of options.allowedOrigins ?? []) {
    try {
      if (normalizeOrigin(allowedOrigin) === parsed.origin) {
        return true;
      }
    } catch {
      // Ignore invalid allowlist entries.
    }
  }

  const hostname = parsed.hostname.toLowerCase();
  const siteProtocol = new URL(siteOrigin).protocol;
  return (options.allowedOriginSuffixes ?? []).some((rawSuffix) => {
    const suffix = normalizeHostnameSuffix(rawSuffix);
    const hostnameMatches = Boolean(suffix) && (hostname === suffix || hostname.endsWith(`.${suffix}`));
    return hostnameMatches && (siteProtocol !== "https:" || parsed.protocol === "https:");
  });
}

function resolveFallbackRedirectUri(
  rawFallback: string,
  siteOrigin: string,
  blockedPaths: string[],
): string {
  const rootFallback = new URL("/", siteOrigin).toString();

  try {
    const parsed = new URL(rawFallback, siteOrigin);
    if (parsed.origin !== siteOrigin || !["http:", "https:"].includes(parsed.protocol)) {
      return rootFallback;
    }
    if (isBlockedLocalPath(parsed.pathname, blockedPaths)) {
      return rootFallback;
    }
    return parsed.toString();
  } catch {
    return rootFallback;
  }
}

export function buildAuthUrl(authUrl: string, options: BuildAuthUrlOptions = {}): string {
  const url = new URL(authUrl);
  url.pathname = resolveAuthPath(url.pathname, options.path);

  for (const [key, value] of Object.entries(options.params ?? {})) {
    if (value === null || value === undefined) continue;
    if (Array.isArray(value)) {
      url.searchParams.delete(key);
      for (const item of value) {
        url.searchParams.append(key, String(item));
      }
      continue;
    }
    url.searchParams.set(key, String(value));
  }

  if (options.mode === "signup") {
    url.searchParams.set("show", "signup");
  }

  if (options.redirectUri && options.redirectUri.trim()) {
    url.searchParams.set("redirect_uri", options.redirectUri);
  }

  return url.toString();
}

export function buildFederatedLogoutUrl(
  authUrl: string,
  options: BuildFederatedLogoutUrlOptions = {},
): string {
  const url = new URL(authUrl);
  url.pathname = resolveAuthPath(url.pathname, options.path ?? "logout");

  for (const [key, value] of Object.entries(options.params ?? {})) {
    if (value === null || value === undefined) continue;
    if (Array.isArray(value)) {
      url.searchParams.delete(key);
      for (const item of value) {
        url.searchParams.append(key, String(item));
      }
      continue;
    }
    url.searchParams.set(key, String(value));
  }

  if (options.redirectUri && options.redirectUri.trim()) {
    url.searchParams.set(options.redirectParam ?? "redirect_uri", options.redirectUri);
  }

  return url.toString();
}

export function resolveSafeRedirectUri(
  rawValue: string | null | undefined,
  options: ResolveSafeRedirectUriOptions,
): string {
  const siteOrigin = normalizeOrigin(options.siteOrigin);
  const blockedPaths = options.blockedPaths ?? DEFAULT_BLOCKED_REDIRECT_PATHS;
  const fallbackTarget = options.fallback ?? "/";
  const fallback = resolveFallbackRedirectUri(fallbackTarget, siteOrigin, blockedPaths);

  if (!rawValue || !rawValue.trim()) {
    return fallback;
  }

  try {
    const parsed = new URL(rawValue, siteOrigin);
    if (!["http:", "https:"].includes(parsed.protocol)) {
      return fallback;
    }
    if (!isAllowedRedirectOrigin(parsed, siteOrigin, options)) {
      return fallback;
    }
    if (parsed.origin === siteOrigin && isBlockedLocalPath(parsed.pathname, blockedPaths)) {
      return fallback;
    }
    return parsed.toString();
  } catch {
    return fallback;
  }
}

function toHeaderRecord(headers?: HeadersInit): Record<string, string> {
  if (!headers) return {};

  if (typeof Headers !== "undefined" && headers instanceof Headers) {
    const result: Record<string, string> = {};
    headers.forEach((value, key) => {
      result[key] = value;
    });
    return result;
  }

  if (Array.isArray(headers)) {
    return Object.fromEntries(headers);
  }

  return { ...(headers as Record<string, string>) };
}

function setHeader(headers: Record<string, string>, name: string, value: string): void {
  const existingName = Object.keys(headers).find((key) => key.toLowerCase() === name.toLowerCase());
  headers[existingName ?? name] = value;
}

function hasHeader(headers: Record<string, string>, name: string): boolean {
  return Object.keys(headers).some((key) => key.toLowerCase() === name.toLowerCase());
}

/** SuperTokens' host-only session markers; the update marker never expires. */
const UPDATE_MARKER_COOKIE = "st-last-access-token-update";
const FRONT_TOKEN_COOKIE = "sFrontToken";

function hasCookie(name: string): boolean {
  return document.cookie.split(";").some((part) => part.trim().startsWith(`${name}=`));
}

/** The update marker with no front token: SuperTokens' "no session", decided without asking. */
export function isHalfCleared(): boolean {
  if (typeof document === "undefined") return false;
  try {
    return hasCookie(UPDATE_MARKER_COOKIE) && !hasCookie(FRONT_TOKEN_COOKIE);
  } catch {
    // A sandboxed frame without allow-same-origin throws on any cookie read.
    // It has no marker to recover, and the check must still settle.
    return false;
  }
}

/** Forget the stale answer, so SuperTokens asks the server the next time. */
export function dropUpdateMarker(): void {
  document.cookie = `${UPDATE_MARKER_COOKIE}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
}

/**
 * Whether this page came by a way the stale marker loops through.
 *
 * Two shapes: back from the sign-in portal, and framed by the workspace --
 * one origin, the portal's -- plus the app's own pages in between. A page
 * typed, bookmarked or reached from a search is neither, and recovering there
 * would cost every signed-out visitor a refused refresh per load; a signed-in
 * one sees the app's sign-in once, and the trip back from the portal
 * recovers. Never on the portal's own host: signing in there writes that
 * host's markers itself, so it cannot loop.
 */
function cameFromThisSite(authUrl: string): boolean {
  try {
    const here = window.location.origin;
    const portal = new URL(authUrl, window.location.href).origin;
    if (here === portal) return false;
    const from = document.referrer ? new URL(document.referrer).origin : "";
    return from === portal || from === here;
  } catch {
    return false;
  }
}

/** Set once a half-cleared session recovery has been tried in this page. */
let markerRecoveryTried = false;

/** For tests: forget that recovery was tried. */
export function resetMarkerRecoveryForTests(): void {
  markerRecoveryTried = false;
}

export class AuthManager {
  private readonly apiUrl: string;
  private readonly authUrl: string;
  private injectedToken: string | null;
  /** An app framed inside an AI tool, whose token comes from the Lemma view around it. */
  private readonly embedded: EmbeddedCredentials | null;
  private state: AuthState = { status: "loading", user: null };
  private listeners: Set<AuthListener> = new Set();
  private authCheckPromise: Promise<AuthState> | null = null;
  private authRevision = 0;
  private readonly onUnauthorised = () => this.markUnauthenticated();

  /**
   * @param token A credential to present as `Authorization: Bearer`. Supplying
   *   it is the supported way to authenticate outside a browser, where there
   *   is no session cookie and no `localStorage`. It wins over a token set
   *   through `setTestingToken`, because it was passed for this client rather
   *   than left lying in shared state.
   */
  constructor(apiUrl: string, authUrl: string, token?: string | null) {
    this.apiUrl = apiUrl;
    this.authUrl = authUrl;
    this.injectedToken = token?.trim() || detectInjectedToken();
    this.embedded = !this.injectedToken && isEmbeddedInHost() ? new EmbeddedCredentials() : null;

    if (!this.injectedToken && !this.embedded) {
      ensureCookieSessionSupport(this.apiUrl, this.onUnauthorised);
    }
  }

  /** Whether requests carry a Bearer token rather than the session cookie. */
  get isTokenMode(): boolean {
    return this.injectedToken !== null || this.embedded !== null;
  }

  /** The Bearer token requests carry right now, if token-mode auth is active. */
  getBearerToken(): string | null {
    return this.injectedToken ?? this.embedded?.current() ?? null;
  }

  /**
   * Resolves once requests can carry credentials: at once, except in an app
   * framed inside an AI tool, which waits for its first token from the view.
   */
  async ready(): Promise<void> {
    if (this.embedded) await this.embedded.ready();
  }

  /**
   * In a framed app, a fresh token from the view after a 401 — the one it held
   * may have outlived itself. False anywhere else, and when none came.
   */
  async renewEmbeddedToken(): Promise<boolean> {
    if (!this.embedded) return false;
    try {
      await this.embedded.renew();
      return true;
    } catch {
      return false;
    }
  }

  /** The current auth state. */
  getState(): AuthState {
    return this.state;
  }

  /** True if currently authenticated (status === "authenticated"). */
  isAuthenticated(): boolean {
    return this.state.status === "authenticated";
  }

  /** Subscribe to auth state changes. Returns an unsubscribe function. */
  subscribe(listener: AuthListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private notify(): void {
    this.listeners.forEach((l) => l(this.state));
  }

  private setState(state: AuthState): void {
    this.state = state;
    this.notify();
  }

  private assertBrowserContext(): void {
    if (typeof window === "undefined") {
      throw new Error("This auth method is only available in browser environments.");
    }
  }

  private getCookie(name: string): string | undefined {
    if (typeof document === "undefined") return undefined;
    const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const match = document.cookie.match(new RegExp(`(?:^|; )${escaped}=([^;]*)`));
    return match ? decodeURIComponent(match[1]) : undefined;
  }

  private getCookieDomainCandidates(): Array<string | undefined> {
    if (typeof window === "undefined") {
      return [undefined];
    }

    const host = window.location.hostname;
    const isIpv4 = /^\d{1,3}(?:\.\d{1,3}){3}$/.test(host);
    const isIpv6 = host.includes(":");
    if (!host || host === "localhost" || isIpv4 || isIpv6) {
      return [undefined];
    }

    const domains = new Set<string>();
    const parts = host.split(".").filter(Boolean);
    for (let i = 0; i < parts.length - 1; i += 1) {
      const candidate = parts.slice(i).join(".");
      if (!candidate) continue;
      domains.add(candidate);
      domains.add(`.${candidate}`);
    }

    return [undefined, ...domains];
  }

  private expireCookie(name: string, domain?: string): void {
    if (typeof document === "undefined") return;
    const domainPart = domain ? `;domain=${domain}` : "";
    document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:00 GMT;max-age=0;path=/${domainPart};samesite=lax`;
    document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:00 GMT;max-age=0;path=/${domainPart}`;
  }

  /**
   * Defensive cleanup for stale SuperTokens frontend marker cookies/storage.
   * This helps recover when signout/session-expiry paths leave local markers behind.
   */
  private clearFrontendSessionMarkers(): void {
    if (typeof window === "undefined") return;

    for (const key of SUPERTOKENS_FRONTEND_MARKER_KEYS) {
      try {
        window.localStorage.removeItem(key);
      } catch {
        // ignore storage errors
      }
      try {
        window.sessionStorage.removeItem(key);
      } catch {
        // ignore storage errors
      }
    }

    const domains = this.getCookieDomainCandidates();
    for (const key of SUPERTOKENS_FRONTEND_MARKER_KEYS) {
      for (const domain of domains) {
        this.expireCookie(key, domain);
      }
    }
  }

  private applyUnauthenticatedState(): AuthState {
    const next: AuthState = { status: "unauthenticated", user: null };
    this.setState(next);
    return next;
  }

  private clearInjectedToken(): void {
    this.injectedToken = null;
    clearTestingToken();
  }

  private async rawSignOutViaBackend(): Promise<void> {
    const antiCsrf = this.getCookie("sAntiCsrf");
    const headers: Record<string, string> = {
      Accept: "application/json",
      "Content-Type": "application/json",
      rid: "anti-csrf",
      "fdi-version": "4.2",
      "st-auth-mode": "cookie",
    };

    if (antiCsrf) {
      headers["anti-csrf"] = antiCsrf;
    }

    const separator = this.apiUrl.includes("?") ? "&" : "?";
    const signOutUrl = `${this.apiUrl.replace(/\/$/, "")}/st/auth/signout${separator}superTokensDoNotDoInterception=true`;

    await fetch(signOutUrl, {
      method: "POST",
      credentials: "include",
      headers,
    });
  }

  /**
   * Check whether a cookie-backed session is active without mutating auth state.
   */
  async isAuthenticatedViaCookie(): Promise<boolean> {
    if (this.isTokenMode) {
      return this.isAuthenticated();
    }

    try {
      const response = await fetch(`${this.apiUrl}/users/me`, {
        method: "GET",
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      return response.status !== 401;
    } catch {
      // A failed verification is not proof that the server revoked the session.
      return true;
    }
  }

  /**
   * Return a browser access token from the session layer.
   * Throws if no token is available.
   */
  async getAccessToken(): Promise<string> {
    if (this.injectedToken) {
      return this.injectedToken;
    }
    if (this.embedded) {
      return this.embedded.ready();
    }

    this.assertBrowserContext();
    ensureCookieSessionSupport(this.apiUrl, this.onUnauthorised);

    const token = await Session.getAccessToken();
    if (!token) {
      throw new Error("Token unavailable");
    }
    return token;
  }

  /**
   * Force a refresh-token flow and return the new access token.
   */
  async refreshAccessToken(): Promise<string> {
    if (this.injectedToken) {
      return this.injectedToken;
    }
    if (this.embedded) {
      return this.embedded.renew();
    }

    this.assertBrowserContext();
    ensureCookieSessionSupport(this.apiUrl, this.onUnauthorised);

    const refreshed = await Session.attemptRefreshingSession();
    if (!refreshed) {
      throw new Error("Session refresh failed");
    }

    const token = await Session.getAccessToken();
    if (!token) {
      throw new Error("Token unavailable");
    }

    return token;
  }

  /**
   * Build request headers for an API call.
   * Uses Bearer token if one was injected, otherwise omits Authorization
   * and lets cookies carry the session.
   */
  getRequestInit(init: RequestInit = {}): RequestInit {
    const headers = toHeaderRecord(init.headers);
    if (!hasHeader(headers, "Accept")) {
      setHeader(headers, "Accept", "application/json");
    }

    const isFormData = typeof FormData !== "undefined" && init.body instanceof FormData;
    if (init.body !== undefined && !isFormData && !hasHeader(headers, "Content-Type")) {
      setHeader(headers, "Content-Type", "application/json");
    }

    const bearer = this.getBearerToken();
    if (bearer) {
      setHeader(headers, "Authorization", `Bearer ${bearer}`);
    }

    return {
      ...init,
      credentials: this.isTokenMode ? "omit" : "include",
      headers,
    };
  }

  /**
   * Call GET /users/me to determine auth state.
   * Sets internal state and notifies listeners.
   */
  checkAuth(): Promise<AuthState> {
    if (this.authCheckPromise) {
      return this.authCheckPromise;
    }

    const checking = this.performAuthCheck(this.authRevision).finally(() => {
      if (this.authCheckPromise === checking) this.authCheckPromise = null;
    });
    this.authCheckPromise = checking;
    return checking;
  }

  /**
   * One refresh for a pod app whose host remembers a session that ended.
   *
   * The session is shared between hosts by the HttpOnly cookies, but the
   * markers the browser SDK reads (`sFrontToken`, `st-last-access-token-update`)
   * are host-only on purpose, so a pod app keeps its own copy. If that copy is
   * half-cleared -- the update marker left behind with no front token, as a
   * failed refresh leaves it -- `doesSessionExist()` answers "no" without ever
   * asking. Signing in again renews the shared cookies but cannot reach this
   * host's marker, so the auth portal, which sees the session, sends the person
   * straight back to an app that does not: a redirect loop with no way out.
   * Drop the stale marker on this host and ask once: the refresh carries the
   * shared cookie and returns this host's own front token.
   *
   * Whether the API is on this origin or another one does not matter: the
   * marker is always this host's, and the refresh cookie travels to the API
   * either way. Once per page, so a genuinely signed-out app costs one refused
   * refresh per load rather than a storm.
   *
   * The refresh is asked for directly, not through `doesSessionExist()`, which
   * folds a network error or a 5xx into "no": an API mid-deploy would sign the
   * person out instead of being waited out. A refresh that fails that way
   * throws, and the caller reads it as unreachable.
   */
  private async recoverHalfClearedSession(): Promise<boolean> {
    if (markerRecoveryTried || typeof document === "undefined") return false;
    markerRecoveryTried = true;
    dropUpdateMarker();
    return Session.attemptRefreshingSession();
  }

  /**
   * Whether the API answers at all -- its liveness probe, not a session check.
   * What a retry after `unreachable` should wait on, so that an outage does
   * not spend the refresh breaker's budget and trip it into a sign-out.
   */
  isReachable(): Promise<boolean> {
    return probeReachable(this.apiUrl);
  }

  /**
   * The local session, with the reason when there is none.
   *
   * `doesSessionExist()` folds every failed refresh into "no": a 401, a server
   * that did not answer, and SuperTokens' duplicate-cookie answer -- a 200
   * with no `front-token`, which the SDK throws on without saving anything.
   * One direct refresh tells them apart. It also is the retry the duplicate
   * answer needs: the server cleared the stray copy on that response, so this
   * refresh carries one cookie and succeeds.
   *
   * A half-cleared host takes the recovery instead of that refresh. There,
   * `attemptRefreshingSession()` never reaches the network: it answers from
   * the marker and fires `UNAUTHORISED` on the way out, which marks this
   * manager signed out and so discards the result of the very check that is
   * about to repair the session.
   */
  private async localSession(): Promise<"exists" | "absent" | "unreachable"> {
    // Read first: `doesSessionExist()` may itself refresh, fail, and leave the
    // marker behind, and then the server has already given its answer.
    const halfCleared = isHalfCleared();
    try {
      if (await Session.doesSessionExist()) return "exists";
    } catch (error) {
      return refreshFailureKind(error);
    }
    if (halfCleared) {
      // The refresh below would answer from the marker too, and sign out.
      if (!cameFromThisSite(this.authUrl)) return "absent";
      try {
        return (await this.recoverHalfClearedSession()) ? "exists" : "absent";
      } catch (error) {
        return refreshFailureKind(error);
      }
    }
    try {
      if (await Session.attemptRefreshingSession()) return "exists";
    } catch (error) {
      if (refreshFailureKind(error) === "unreachable") return "unreachable";
    }
    return "absent";
  }

  private async performAuthCheck(revision: number): Promise<AuthState> {
    const unauthenticated = (): AuthState => revision === this.authRevision
      ? this.applyUnauthenticatedState()
      : this.state;
    const unreachable = (): AuthState => {
      if (revision !== this.authRevision) return this.state;
      const next: AuthState = { status: "unreachable", user: null };
      this.setState(next);
      return next;
    };
    this.setState({ status: "loading", user: null });

    // Cookie mode: short-circuit when no session exists locally instead of
    // hitting /users/me. A 401 there makes the SuperTokens fetch interceptor
    // treat it as "refresh me" and hammer the session-refresh endpoint — and
    // when the app's configured auth domain can't share cookies with the
    // current origin (e.g. an embedded app booting on localhost), that refresh
    // can never succeed, so every app on the pod home storms it endlessly.
    // `doesSessionExist()` reads the local front token only (no network) and
    // returns false when there's nothing to refresh, ending the loop at the source.
    if (!this.isTokenMode && typeof window !== "undefined") {
      ensureCookieSessionSupport(this.apiUrl, this.onUnauthorised);
      const local = await this.localSession();
      if (local === "unreachable") return unreachable();
      if (local === "absent") return unauthenticated();
    }
    if (this.embedded) {
      try {
        await this.embedded.ready();
      } catch {
        return unauthenticated();
      }
    }

    if (revision !== this.authRevision) return this.state;

    try {
      const response = await fetch(
        `${this.apiUrl}/users/me`,
        this.getRequestInit({ method: "GET" }),
      );

      // Only 401 means not authenticated — 403 means authenticated but forbidden
      if (response.status === 401) {
        return unauthenticated();
      }

      // A server restarting or a gateway with nothing behind it: no answer
      // about the session at all, so not a reason to sign anyone out.
      if (isUnreachableStatus(response.status)) {
        return unreachable();
      }

      if (!response.ok) {
        // For other non-401 errors on /users/me, treat as unauthenticated (conservative)
        return unauthenticated();
      }

      const user = (await response.json()) as UserInfo;
      if (revision !== this.authRevision) return this.state;
      const next: AuthState = { status: "authenticated", user };
      this.setState(next);
      return next;
    } catch (error) {
      // The request never got an answer, or the refresh it triggered did not.
      return refreshFailureKind(error) === "unreachable" ? unreachable() : unauthenticated();
    }
  }

  /**
   * Mark the session as unauthenticated (e.g. after a 401 response).
   * Does NOT redirect — call redirectToAuth() explicitly if desired.
   */
  markUnauthenticated(): void {
    this.authRevision += 1;
    this.authCheckPromise = null;
    this.applyUnauthenticatedState();
  }

  /**
   * Sign out the current user session.
   * Returns true when the session is no longer active.
   */
  async signOut(): Promise<boolean> {
    // A response started before logout must never restore the departing user.
    this.authRevision += 1;
    this.authCheckPromise = null;
    if (this.injectedToken) {
      this.clearInjectedToken();
      this.markUnauthenticated();
      return true;
    }
    // A framed app holds no Lemma session to end; the view's connection is
    // ended where it was made, in the AI tool or in Lemma's settings.
    if (this.embedded) {
      this.markUnauthenticated();
      return true;
    }

    this.assertBrowserContext();
    ensureCookieSessionSupport(this.apiUrl, this.onUnauthorised);

    try {
      await Session.signOut();
    } catch {
      // continue with raw fallback
    }

    if (await this.isAuthenticatedViaCookie()) {
      try {
        await this.rawSignOutViaBackend();
      } catch {
        // best effort fallback only
      }
    }

    // Always clear frontend markers on logout attempt, even if backend session
    // cleanup is partial. This avoids stale local "EXISTS" signals.
    this.clearFrontendSessionMarkers();

    const isAuthenticated = await this.isAuthenticatedViaCookie();
    if (!isAuthenticated) {
      this.markUnauthenticated();
    }
    return !isAuthenticated;
  }

  /**
   * Build auth URL for login/signup/custom auth sub-path.
   */
  getAuthUrl(options: BuildAuthUrlOptions = {}): string {
    return buildAuthUrl(this.authUrl, options);
  }

  /**
   * Build upstream/federated logout URL.
   */
  getFederatedLogoutUrl(options: BuildFederatedLogoutUrlOptions = {}): string {
    return buildFederatedLogoutUrl(this.authUrl, options);
  }

  /**
   * Redirect to the auth service, passing the current URL as redirect_uri.
   * After the user authenticates, the auth service should redirect back to
   * the original URL and set the session cookie.
   */
  redirectToAuth(options: Omit<BuildAuthUrlOptions, "redirectUri"> & { redirectUri?: string } = {}): void {
    if (typeof window === "undefined") {
      return;
    }
    const redirectUri = options.redirectUri ?? window.location.href;
    const url = this.getAuthUrl({ ...options, redirectUri });
    // The sign-in page refuses every frame (`frame-ancestors 'none'`), so an
    // app shown in a workspace pane that navigated itself landed on the
    // browser's "refused to connect" page. Sign in at the top instead, as a
    // private app's access page does.
    if (window.top && window.top !== window.self) {
      try {
        window.top.location.href = url;
        return;
      } catch {
        // A sandbox that withholds top navigation: the frame is all there is.
      }
    }
    window.location.href = url;
  }

  /**
   * Optional full logout flow:
   * 1. clear local SDK/session cookies
   * 2. redirect to auth service logout endpoint to terminate upstream SSO
   */
  async redirectToFederatedLogout(options: RedirectToFederatedLogoutOptions = {}): Promise<void> {
    this.assertBrowserContext();

    const redirectUri = options.redirectUri ?? window.location.href;
    const localSignOut = options.localSignOut ?? true;

    if (localSignOut) {
      await this.signOut();
    }

    window.location.href = this.getFederatedLogoutUrl({
      ...options,
      redirectUri,
    });
  }
}
