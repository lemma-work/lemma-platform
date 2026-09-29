import { lemma } from "@/session/client";

/** Chatting with your agents on Telegram, through this Lemma's own bot.
 *
 *  The Telegram token saved in Server setup is the server's shared bot, and a
 *  stranger writing to it is asked to prove who they are by email — which a
 *  Mac with no mail set up can never finish. The person at this Mac is already
 *  signed in, so the app vouches for them instead: it mints a one-time link for
 *  the signed-in user and opens it in Telegram, and pressing Start there links
 *  that chat. Pure apart from the two calls, which are injected so the tests
 *  can stand in for the API. */

export interface LinkablePod {
    id: string;
    name: string;
}

export interface TelegramLinkOptions {
    botUsername: string;
    pods: LinkablePod[];
    /** The pod that answers when none is picked. */
    podId: string | null;
}

export function readLinkOptions(raw: unknown): TelegramLinkOptions {
    const answer = (raw ?? {}) as { bot_username?: unknown; pods?: unknown; pod_id?: unknown };
    const pods = Array.isArray(answer.pods) ? answer.pods : [];
    return {
        botUsername: String(answer.bot_username ?? "").replace(/^@/, ""),
        pods: pods
            .map((pod) => pod as { id?: unknown; name?: unknown })
            .filter((pod) => typeof pod.id === "string" && pod.id)
            .map((pod) => ({ id: String(pod.id), name: String(pod.name ?? "") })),
        podId: typeof answer.pod_id === "string" && answer.pod_id ? answer.pod_id : null,
    };
}

export async function telegramLinkOptions(
    call: () => Promise<unknown> = () => lemma().userSurfaces.telegramLinkOptions(),
): Promise<TelegramLinkOptions> {
    return readLinkOptions(await call());
}

/** Only a link that opens Telegram is ever handed to the browser. The URL is
 *  the API's, and a link minted by this server has exactly this shape; anything
 *  else is refused rather than opened. */
const TELEGRAM_LINK = /^https:\/\/t\.me\/[A-Za-z0-9_]{3,64}\?start=link_[A-Za-z0-9_-]+$/;

/** A fresh link for the signed-in user, answered from `podId` (or the suggested
 *  pod when null). Resolves to the `t.me` URL to open. */
export async function mintTelegramLink(
    podId: string | null,
    call: (body: { pod_id?: string }) => Promise<unknown> = (body) => lemma().userSurfaces.createTelegramLink(body),
): Promise<string> {
    const answer = (await call(podId ? { pod_id: podId } : {})) as { url?: unknown } | null;
    const url = String(answer?.url ?? "");
    if (!TELEGRAM_LINK.test(url)) throw new Error("Lemma didn’t return a Telegram link. Try again.");
    return url;
}

/** What the Telegram card shows under the saved token.
 *
 *  Nothing while the token is unsaved or being changed: the bot the API would
 *  name is the one still running, not the one being typed. */
export type TelegramChatCard =
    | { kind: "hidden" }
    | { kind: "loading" }
    | { kind: "problem"; text: string }
    | { kind: "ready"; title: string; answers: string; pods: LinkablePod[] };

export function telegramChatCard({ saved, unsaved, options, problem, podId }: {
    saved: boolean;
    unsaved: boolean;
    options: TelegramLinkOptions | undefined;
    problem: unknown;
    /** The pod picked here, if one was. */
    podId: string | null;
}): TelegramChatCard {
    if (!saved || unsaved) return { kind: "hidden" };
    if (problem) return { kind: "problem", text: linkProblem(problem) };
    if (!options) return { kind: "loading" };
    return {
        kind: "ready",
        title: "@" + options.botUsername + " is ready",
        answers: whoAnswers(options, podId),
        pods: options.pods,
    };
}

/** The one line saying who picks up. */
export function whoAnswers(options: TelegramLinkOptions, podId: string | null): string {
    const chosen = options.pods.find((pod) => pod.id === (podId ?? options.podId));
    if (chosen) return "Your " + chosen.name + " agent answers.";
    return "Your agent answers from a new personal pod.";
}

export function linkProblem(problem: unknown): string {
    const message = problem instanceof Error ? problem.message : typeof problem === "string" ? problem : "";
    return message || "Couldn’t reach Lemma’s server to set up the Telegram link.";
}
