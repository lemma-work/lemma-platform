import { buildApiUrl } from "@/components/auth/portal/auth/config";

type AltchaPurpose = "signup" | "verification" | "password-reset" | "signin-risk";

type AltchaChallenge = {
  enabled: boolean;
  algorithm?: "SHA-256";
  challenge?: string;
  maxnumber?: number;
  salt?: string;
  signature?: string;
};

export type AltchaProgress = {
  phase: "idle" | "checking" | "solving" | "complete" | "error";
  enabled: boolean | null;
};

type Fetcher = typeof fetch;

let progress: AltchaProgress = { phase: "idle", enabled: null };
const progressListeners = new Set<() => void>();

function updateProgress(next: AltchaProgress): void {
  progress = next;
  progressListeners.forEach((listener) => listener());
}

export function getAltchaProgress(): AltchaProgress {
  return progress;
}

export function subscribeAltchaProgress(listener: () => void): () => void {
  progressListeners.add(listener);
  return () => progressListeners.delete(listener);
}

export async function fetchAltchaEnabled(
  fetcher: Fetcher = fetch,
): Promise<boolean | null> {
  try {
    const response = await fetcher(buildApiUrl("/auth/altcha/config"), {
      credentials: "include",
    });
    if (!response.ok) return null;
    const payload = (await response.json()) as { enabled?: boolean };
    return typeof payload.enabled === "boolean" ? payload.enabled : null;
  } catch {
    return null;
  }
}

function hex(bytes: ArrayBuffer): string {
  return Array.from(new Uint8Array(bytes), (value) => value.toString(16).padStart(2, "0")).join("");
}

async function solve(challenge: AltchaChallenge): Promise<string | null> {
  if (!challenge.enabled) return null;
  if (
    challenge.algorithm !== "SHA-256" ||
    !challenge.challenge ||
    !challenge.salt ||
    !challenge.signature ||
    challenge.maxnumber === undefined
  ) {
    throw new Error("The proof-of-work challenge is invalid");
  }
  const encoder = new TextEncoder();
  let number = 0;
  for (; number <= challenge.maxnumber; number += 1) {
    const digest = await crypto.subtle.digest(
      "SHA-256",
      encoder.encode(`${challenge.salt}${number}`),
    );
    if (hex(digest) === challenge.challenge) break;
    if (number % 1000 === 0) {
      await new Promise((resolve) => globalThis.setTimeout(resolve, 0));
    }
  }
  if (number > challenge.maxnumber) {
    throw new Error("Unable to solve the proof-of-work challenge");
  }
  const payload = JSON.stringify({
    algorithm: challenge.algorithm,
    challenge: challenge.challenge,
    number,
    salt: challenge.salt,
    signature: challenge.signature,
  });
  return btoa(payload).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function addAltchaProof(
  requestInit: RequestInit,
  purpose: AltchaPurpose,
  fetcher: Fetcher = fetch,
): Promise<RequestInit> {
  updateProgress({ phase: "checking", enabled: progress.enabled });
  try {
    const response = await fetcher(
      buildApiUrl(`/auth/altcha/challenge?purpose=${encodeURIComponent(purpose)}`),
      { credentials: "include" },
    );
    if (!response.ok) {
      throw new Error("Authentication protection is temporarily unavailable");
    }
    const challenge = (await response.json()) as AltchaChallenge;
    if (!challenge.enabled) {
      updateProgress({ phase: "idle", enabled: false });
      return requestInit;
    }

    updateProgress({ phase: "solving", enabled: true });
    const proof = await solve(challenge);
    if (!proof) return requestInit;
    const headers = new Headers(requestInit.headers);
    headers.set("x-altcha-payload", proof);
    updateProgress({ phase: "complete", enabled: true });
    return { ...requestInit, headers };
  } catch (error) {
    updateProgress({ phase: "error", enabled: progress.enabled });
    throw error;
  }
}
