import type { HttpClient } from "../http.js";

export interface WebLogin {
    /**
     * The site, as a person would name it.
     *
     * Grouped by registrable domain, so `lemma.work` and `api.lemma.work` are one
     * login rather than two — the second being the half nobody visited on
     * purpose.
     */
    site: string;
    /** How many cookies it has. A rough sense of scale, not a health check. */
    cookie_count: number;
    /**
     * When the soonest of them lapses, which is the closest a browser can come
     * to "when will I have to sign in again". Null when they are all session
     * cookies.
     */
    expires: string | null;
    /**
     * Whether somebody answered "yes, I signed in" to a sign-in request for
     * this site.
     *
     * The cookies cannot say this on their own, and that is measured rather
     * than assumed: a real profile held two HttpOnly session cookies for a
     * site somebody was signed in to, and six HttpOnly cookies for one that
     * had merely had a video played on it. `false` means "nobody said so", not
     * "no session" — the browser may well still be signed in.
     */
    signed_in: boolean;
}

/** An agent waiting for somebody to sign a site in.
 *
 * There is no status here and no id of its own: the paused tool call is the
 * request, so "still waiting" is whether this comes back at all.
 */
export interface PendingSignIn {
    tool_call_id: string;
    origin: string;
    /** What the agent is doing, in its own words, to show the person. */
    reason: string;
}

/** What came of somebody answering. */
export interface SignInOutcome {
    origin: string;
    signed_in: boolean;
    /**
     * Whether the site stopped asking for a login straight afterwards.
     * Reported, not enforced — the check is a heuristic and the person has
     * already done what was asked.
     */
    working: boolean;
}

/**
 * The sites your sandbox's browser is signed in to.
 *
 * Read from the browser every time, not from a table: it keeps its own
 * profile, so what it holds is the only true answer. Nothing here returns a
 * secret, and that is now structural rather than a promise — cookie values
 * never leave the sandbox at all.
 */
export class WebLoginsNamespace {
    constructor(private readonly http: HttpClient) {}

    /**
     * Every site the browser is signed in to.
     *
     * Not paged: this is what one browser is holding, not a table that grows.
     * `wake` is off by default so that rendering the list is never what starts
     * somebody's computer — a paused one answers `sleeping`.
     */
    list(
        options: { wake?: boolean } = {},
    ): Promise<{ items: WebLogin[]; sleeping: boolean }> {
        const params: Record<string, string | number> = {};
        if (options.wake) params.wake = "true";
        return this.http.request("GET", "/web-logins", { params });
    }

    /**
     * Sign the browser out of a site.
     *
     * Really signs it out, which its predecessor did not: that removed Lemma's
     * encrypted copy and left the browser as it was. Needs the computer
     * running, and says so rather than reporting a success it did not achieve.
     */
    remove(origin: string): Promise<{ site: string; forgotten: boolean }> {
        return this.http.request("DELETE", "/web-logins", {
            params: { origin },
        });
    }

    /** What a sign-in link is asking for, addressed by the pause it is for.
     *
     * The conversation and tool call are a lookup, not a credential: the server
     * resolves both against the caller's own session, so a forwarded link answers
     * exactly as an invented one does.
     */
    pendingSignIn(
        conversationId: string,
        toolCallId: string,
    ): Promise<PendingSignIn> {
        return this.http.request<PendingSignIn>(
            "GET",
            `/web-logins/sign-ins/${encodeURIComponent(conversationId)}/${encodeURIComponent(toolCallId)}`,
        );
    }

    /**
     * Say whether you signed in, so the waiting run can carry on.
     *
     * One call for both answers because it is one answer. Nothing is stored:
     * the browser holds the session, so finishing is the person finishing. The
     * reply says whether the site stopped asking, which the agent is told.
     */
    answerSignIn(
        conversationId: string,
        toolCallId: string,
        options: { signedIn: boolean },
    ): Promise<SignInOutcome> {
        return this.http.request<SignInOutcome>(
            "POST",
            `/web-logins/sign-ins/${encodeURIComponent(conversationId)}/${encodeURIComponent(toolCallId)}/answer`,
            { body: { signed_in: options.signedIn } },
        );
    }
}
