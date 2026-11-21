"use client";

import { useEffect, useRef, useState } from "react";
import { LoadingIndicator } from "@/ui/loading";
import { Chip, Screen, type Speaker } from "./frame";
import { Glyph } from "./marks";
import { Session } from "./supertokens";
import { authLink } from "./redirects";
import { PORTAL_PATH } from "./config";
import { accountAccess, completionDestination, signedInEmail } from "./completion";
import {
    EVENTS_SCOPE,
    WRITE_SCOPE,
    answerConsentRequest,
    dropConsentRequest,
    heldConsentRequest,
    holdConsentRequest,
    readConsentRequest,
    requestIdFromSearch,
    whereBack,
    whoIsAsking,
    type ConsentRequest,
} from "./mcp-consent";

type State = "reading" | "invalid" | "asking" | "answering" | "allowed" | "refused" | "failed";

/** `/auth/authorize`: a person letting an MCP client use one space.
 *  `mcp-consent.ts` has the whole exchange. */
export function McpConsent() {
    const [state, setState] = useState<State>("reading");
    const [request, setRequest] = useState<ConsentRequest | null>(null);
    const [email, setEmail] = useState<string | null>(null);
    const [said, setSaid] = useState<string | null>(null);
    /* Starts at what the app asked for; the person can narrow it here, and
       only narrow it. */
    const [readOnly, setReadOnly] = useState(false);
    /* Off until ticked: rows leave for the app's server as they are added. */
    const [events, setEvents] = useState(false);
    const id = useRef<string | null>(null);

    useEffect(() => {
        const search = window.location.search;
        const found = new URLSearchParams(search).has("request") ? requestIdFromSearch(search) : heldConsentRequest();
        if (!found) {
            dropConsentRequest();
            setState("invalid");
            return;
        }
        id.current = found;
        holdConsentRequest(found);
        let cancelled = false;
        void (async () => {
            try {
                const access = await accountAccess();
                if (access !== "ready") {
                    /* Sign in, or verify, and `landing()` brings this tab back
                       here while the request is held. */
                    window.location.replace(completionDestination(access, ""));
                    return;
                }
                const [asked, who] = await Promise.all([readConsentRequest(found), signedInEmail()]);
                if (cancelled) return;
                setRequest(asked);
                setEmail(who);
                setState("asking");
            } catch (problem) {
                if (cancelled) return;
                dropConsentRequest();
                setSaid(problem instanceof Error ? problem.message : null);
                setState("failed");
            }
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    const answer = async (allow: boolean) => {
        const current = id.current;
        if (!current) return;
        setState("answering");
        try {
            const next = await answerConsentRequest(current, { allow, readOnly, events });
            dropConsentRequest();
            /* Said before leaving: a web app's page replaces this one, but an
               app on this device opens beside it and this tab stays. */
            setState(allow ? "allowed" : "refused");
            window.location.assign(next);
        } catch (problem) {
            dropConsentRequest();
            setSaid(problem instanceof Error ? problem.message : null);
            setState("failed");
        }
    };

    /* What is asking, on the left: the name it can be checked by, and its
       initial in place of Lemma's bars. */
    const who = request ? whoIsAsking(request) : null;
    const speaker = (status?: string, done = false): Speaker | undefined => who
        ? { kind: "other", name: who.title, mark: <span className="auth-other__initial">{who.verified ? who.title.charAt(0).toUpperCase() : "?"}</span>, status, done }
        : undefined;
    const leaving = request?.redirect_to_app ? "Go back to the app. You can close this tab." : request ? "Taking you back to " + whereBack(request) + "…" : undefined;

    switch (state) {
        case "invalid":
            return (
                <Screen
                    icon={{ glyph: "warn", tone: "warn" }}
                    title="This link is incomplete"
                    lead="It doesn’t say which request to answer. Start connecting again from the app you were using."
                />
            );
        case "failed":
            return (
                <Screen
                    speaker={speaker()}
                    icon={{ glyph: "x" }}
                    title="Nothing was connected"
                    lead={said ?? "Something went wrong on the way. Start connecting again from the app you were using."}
                />
            );
        case "allowed":
            return (
                <Screen
                    speaker={speaker("Connected", true)}
                    icon={{ glyph: "check", tone: "ok" }}
                    title={(who?.verified ? who.title : "The app") + " is connected"}
                    lead={leaving}
                />
            );
        case "refused":
            return (
                <Screen
                    speaker={speaker()}
                    icon={{ glyph: "x" }}
                    title="Nothing was connected"
                    lead={<>{who?.verified ? who.title : "The app"} can’t use {request?.pod_name}. {leaving}</>}
                />
            );
        case "asking": {
            if (!request || !who) return null;
            const asksToWrite = request.scopes.includes(WRITE_SCOPE);
            const asksForEvents = request.scopes.includes(EVENTS_SCOPE);
            return (
                <Screen
                    speaker={speaker("Asking to connect")}
                    title={"Let " + (who.verified ? who.title : "an unverified app") + " use " + request.pod_name + "?"}
                    lead={
                        <>
                            {who.claim} After you answer you go back to{" "}
                            <b>{whereBack(request)}</b>. Only go on if that’s where you’re connecting from.
                        </>
                    }
                >
                    {/* What it may do, in plain rows. Starts at what the app
                        asked for; the person can narrow it here, and only
                        narrow it. */}
                    <ul className="auth-asks">
                        <li>
                            <Glyph name="check" size={18} />
                            <span>Read tables and files<small>Only what you can see in {request.pod_name}</small></span>
                        </li>
                        {asksToWrite ? (
                            <li data-no={readOnly ? "" : undefined}>
                                <Glyph name={readOnly ? "minus" : "check"} size={18} />
                                <label className="auth-asks__choice">
                                    <span>
                                        Add, change and delete tables and files
                                        <small>{readOnly ? "Not allowed: it may only read." : "Untick to let it read only."}</small>
                                    </span>
                                    <input type="checkbox" checked={!readOnly} onChange={(event) => setReadOnly(!event.target.checked)} />
                                </label>
                            </li>
                        ) : (
                            <li data-no="">
                                <Glyph name="minus" size={18} />
                                <span>Add, change or delete anything<small>It didn’t ask to.</small></span>
                            </li>
                        )}
                        {asksForEvents && (
                            /* Off until ticked: rows leave for the app's
                               server as they are added. */
                            <li data-no={events ? undefined : ""}>
                                <Glyph name={events ? "check" : "minus"} size={18} />
                                <label className="auth-asks__choice">
                                    <span>
                                        Also tell it when rows are added to tables you can read
                                        <small>
                                            Each new row is sent to the app’s own server as it is added, including
                                            while you are away. You can stop this in {request.pod_name}’s settings.
                                        </small>
                                    </span>
                                    <input type="checkbox" checked={events} onChange={(event) => setEvents(event.target.checked)} />
                                </label>
                            </li>
                        )}
                    </ul>
                    {email && (
                        <Chip email={email} action={
                            <button type="button" className="auth-link" onClick={() => {
                                /* The request stays held, so signing in as
                                   somebody else comes back here. */
                                void Session.signOut().then(() => window.location.replace(authLink(PORTAL_PATH)))
                                    .catch(() => window.location.replace(authLink(PORTAL_PATH)));
                            }}>Not you?</button>
                        } />
                    )}
                    <p className="auth-small">
                        It acts as you and sees only what you can see in {request.pod_name}. You can disconnect it
                        at any time in {request.pod_name}’s settings.
                    </p>
                    <div className="auth-row">
                        <button className="b b--l b--primary" onClick={() => void answer(true)}>
                            Allow
                        </button>
                        <button className="b b--l b--secondary" onClick={() => void answer(false)}>
                            Don’t allow
                        </button>
                    </div>
                </Screen>
            );
        }
        default:
            return (
                <Screen speaker={speaker()} title={state === "reading" ? "Checking your session…" : "Sending your answer…"}>
                    <LoadingIndicator label={state === "reading" ? "Checking your session" : "Sending your answer"} />
                </Screen>
            );
    }
}
