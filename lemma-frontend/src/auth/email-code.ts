import { onApi } from "./config";
import { retryAfterSeconds, sayDelay } from "./errors";

export interface EmailChallenge { challenge_id: string; expires_at: string }
type CodeAction = "browser" | "start" | "resend" | "verify";

export class EmailCodeError extends Error {
    readonly status: number;
    readonly retryAfter: number | null;

    constructor(message: string, status: number, retryAfter: number | null) {
        super(message);
        this.name = "EmailCodeError";
        this.status = status;
        this.retryAfter = retryAfter;
    }
}

function record(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null;
}

async function request(action: CodeAction, body: object, fetcher: typeof fetch): Promise<unknown> {
    const response = await fetcher(onApi("/auth/email-code/" + action), {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json", "st-auth-mode": "cookie" },
        body: JSON.stringify(body),
    });
    const result: unknown = await response.json().catch(() => null);
    if (!response.ok) {
        const message = record(result)
            ? [result.message, result.detail].find((value): value is string => typeof value === "string" && value.trim().length > 0)
            : null;
        const delay = retryAfterSeconds(response.headers.get("retry-after"));
        throw new EmailCodeError((message ?? "Unable to continue. Please try again.") +
            (delay ? " Try again in " + sayDelay(delay) + "." : ""), response.status, delay);
    }
    return result;
}

function challenge(value: unknown): EmailChallenge {
    if (!record(value) || typeof value.challenge_id !== "string" || !value.challenge_id ||
        typeof value.expires_at !== "string" || !Number.isFinite(Date.parse(value.expires_at))) {
        throw new Error("We couldn’t send a code. Please try again.");
    }
    return { challenge_id: value.challenge_id, expires_at: value.expires_at };
}

export async function mintEmailNonce(fetcher: typeof fetch = fetch): Promise<string> {
    const result = await request("browser", {}, fetcher);
    if (!record(result) || typeof result.nonce !== "string" || !result.nonce) {
        throw new Error("We couldn’t start sign-in. Please try again.");
    }
    return result.nonce;
}

export async function startEmailCode(email: string, nonce: string, fetcher: typeof fetch = fetch): Promise<EmailChallenge> {
    return challenge(await request("start", { email: email.trim(), nonce }, fetcher));
}

export async function resendEmailCode(challengeId: string, nonce: string, fetcher: typeof fetch = fetch): Promise<EmailChallenge> {
    return challenge(await request("resend", { challenge_id: challengeId, nonce }, fetcher));
}

export async function verifyEmailCode(challengeId: string, nonce: string, code: string, fetcher: typeof fetch = fetch): Promise<void> {
    if (!/^[0-9]{6}$/.test(code.trim())) throw new Error("Enter the six-digit code from your email.");
    const result = await request("verify", { challenge_id: challengeId, nonce, code: code.trim() }, fetcher);
    if (!record(result) || result.status !== "complete") throw new Error("We couldn’t confirm your code. Please try again.");
}
