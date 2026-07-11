const DESKTOP_REQUEST_STORAGE_KEY = "lemma.desktop-auth.request-id";
const DESKTOP_PENDING_STORAGE_KEY = "lemma.desktop-auth.pending";

export type LemmaDesktopContext = {
  version: string;
  mode: "local" | "hosted" | "undecided";
};

export type PendingDesktopAuth = {
  requestId: string;
  verifier: string;
  browserUrl: string;
  expiresAt: number;
};

declare global {
  interface Window {
    __LEMMA_DESKTOP__?: LemmaDesktopContext;
  }
}

export function isLemmaDesktop(): boolean {
  return typeof window !== "undefined" && Boolean(window.__LEMMA_DESKTOP__);
}

export function readDesktopRequestIdFromSearch(search: string): string | null {
  const value = new URLSearchParams(search).get("desktop_request")?.trim();
  return value && /^[A-Za-z0-9_-]{20,128}$/.test(value) ? value : null;
}

export function storeDesktopRequestId(requestId: string | null): void {
  if (!requestId) return;
  window.sessionStorage.setItem(DESKTOP_REQUEST_STORAGE_KEY, requestId);
}

export function getStoredDesktopRequestId(): string | null {
  const value = window.sessionStorage.getItem(DESKTOP_REQUEST_STORAGE_KEY);
  return value && /^[A-Za-z0-9_-]{20,128}$/.test(value) ? value : null;
}

export function clearStoredDesktopRequestId(): void {
  window.sessionStorage.removeItem(DESKTOP_REQUEST_STORAGE_KEY);
}

export function getPendingDesktopAuth(): PendingDesktopAuth | null {
  const raw = window.sessionStorage.getItem(DESKTOP_PENDING_STORAGE_KEY);
  if (!raw) return null;
  try {
    const pending = JSON.parse(raw) as Partial<PendingDesktopAuth>;
    if (
      typeof pending.requestId !== "string" ||
      typeof pending.verifier !== "string" ||
      typeof pending.browserUrl !== "string" ||
      typeof pending.expiresAt !== "number" ||
      pending.expiresAt <= Date.now()
    ) {
      clearPendingDesktopAuth();
      return null;
    }
    return pending as PendingDesktopAuth;
  } catch {
    clearPendingDesktopAuth();
    return null;
  }
}

export function storePendingDesktopAuth(pending: PendingDesktopAuth): void {
  window.sessionStorage.setItem(
    DESKTOP_PENDING_STORAGE_KEY,
    JSON.stringify(pending),
  );
}

export function clearPendingDesktopAuth(): void {
  window.sessionStorage.removeItem(DESKTOP_PENDING_STORAGE_KEY);
}

export function createDesktopVerifier(): string {
  const bytes = new Uint8Array(32);
  window.crypto.getRandomValues(bytes);
  return base64Url(bytes);
}

export async function challengeForDesktopVerifier(
  verifier: string,
): Promise<string> {
  const digest = await window.crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier),
  );
  return base64Url(new Uint8Array(digest));
}

function base64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return window
    .btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}
