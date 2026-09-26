/** History fetching and a teammate's reply are independent kinds of progress. */
export function transcriptState({ loading, hasTurns, hasStreamingText, error }: {
    loading: boolean;
    hasTurns: boolean;
    hasStreamingText: boolean;
    error: string | null;
}): "loading" | "empty" | "content" | "error" {
    if (hasTurns || hasStreamingText) return "content";
    if (loading) return "loading";
    if (error) return "error";
    return "empty";
}

/** Why a run failed, in words the reader can act on.
 *
 *  A run on a coding agent fails in ways the backend describes in its own
 *  terms — a wait deadline, an unconfirmed delivery, a missing terminal event
 *  — which is exactly right in a log and meaningless on a conversation. Those
 *  are said again here as what happened and what to do, and marked
 *  `codingAgents` so the card can offer the settings where the computer and
 *  its agents are. Anything else is already a sentence and passes through.
 *
 *  The reason is the run's own (`last_run_error`), so it reads the same after
 *  a reload as it did live. */
export interface RunFailure {
    text: string;
    codingAgents: boolean;
}

const CODING_AGENT_FAILURES: { pattern: RegExp; text: string }[] = [
    {
        pattern: /No Agent Host received the run|HOST_WAIT_TIMEOUT|Agent Host harness is unavailable/i,
        text: "The computer this teammate runs on didn’t pick up the task in time. Check that it’s awake and Lemma is open on it, then try again.",
    },
    {
        pattern: /acceptance could not be confirmed|delivery could not be confirmed|HOST_ACCEPTANCE_UNKNOWN/i,
        text: "The task reached the computer, but Lemma couldn’t confirm it started, so it wasn’t repeated. Check the work there before trying again.",
    },
    {
        pattern: /credential was due to expire/i,
        text: "The coding agent ran out of time for this task. Try again to carry on.",
    },
    {
        pattern: /terminal event before the run deadline|without its required terminal event|Agent Host run ended in/i,
        text: "The coding agent stopped reporting before it finished. Try again.",
    },
    {
        pattern: /rejected this run before dispatch/i,
        text: "The computer turned this task down before starting it. Try again.",
    },
    {
        pattern: /revoked this Agent Host|HOST_REVOKED/i,
        text: "This computer was removed from your account, so the task ended.",
    },
    {
        pattern: /Invalid Agent Host runtime profile/i,
        text: "This teammate’s coding-agent settings are no longer valid. Choose its agent again in Models.",
    },
    {
        pattern: /Agent Host/i,
        text: "The coding agent couldn’t finish this task.",
    },
];

export function runFailure(message: string | null | undefined): RunFailure {
    const said = (message ?? "").trim();
    if (!said) return { text: "That run failed.", codingAgents: false };
    const known = CODING_AGENT_FAILURES.find(({ pattern }) => pattern.test(said));
    return known ? { text: known.text, codingAgents: true } : { text: said, codingAgents: false };
}
