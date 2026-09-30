"use client";

import { useEffect, useRef, useState } from "react";
import { LoadingIndicator } from "@/ui/loading";
import { Screen } from "./screens";
import { accountAccess, completionDestination, signedInEmail } from "./completion";
import {
    WRITE_SCOPE,
    answerConsentRequest,
    dropConsentRequest,
    heldConsentRequest,
    holdConsentRequest,
    readConsentRequest,
    requestIdFromSearch,
    whoIsAsking,
    type ConsentRequest,
} from "./mcp-consent";

type State = "reading" | "invalid" | "asking" | "answering" | "failed";

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
            const next = await answerConsentRequest(current, { allow, readOnly });
            dropConsentRequest();
            window.location.assign(next);
        } catch (problem) {
            dropConsentRequest();
            setSaid(problem instanceof Error ? problem.message : null);
            setState("failed");
        }
    };

    switch (state) {
        case "invalid":
            return (
                <Screen
                    title="This link is incomplete"
                    lead="It does not say which request to answer. Start connecting again from the app you were using."
                />
            );
        case "failed":
            return (
                <Screen
                    title="Nothing was connected"
                    lead={said ?? "Something went wrong on the way. Start connecting again from the app you were using."}
                />
            );
        case "asking": {
            if (!request) return null;
            const asksToWrite = request.scopes.includes(WRITE_SCOPE);
            const who = whoIsAsking(request);
            return (
                <Screen
                    title={who.title + " wants to use " + request.pod_name}
                    lead={
                        <>
                            {who.claim} After you answer you will be sent to{" "}
                            <strong>{request.redirect_host}</strong> — only continue if that is where you
                            are connecting from.
                        </>
                    }
                    footer={
                        <>
                            It acts as you{email ? <> ({email})</> : null} and sees only what you can see in{" "}
                            {request.pod_name}. You can disconnect it at any time in the space’s settings.
                        </>
                    }
                >
                    {asksToWrite ? (
                        <fieldset className="auth__choices">
                            <legend>What may it do?</legend>
                            <label>
                                <input type="radio" name="access" checked={!readOnly} onChange={() => setReadOnly(false)} />{" "}
                                Read, add, change and delete tables and files
                            </label>
                            <label>
                                <input type="radio" name="access" checked={readOnly} onChange={() => setReadOnly(true)} />{" "}
                                Read only
                            </label>
                        </fieldset>
                    ) : (
                        <p>It asks to read tables and files only.</p>
                    )}
                    <div className="screen__actions">
                        <button className="btn btn--primary" onClick={() => void answer(true)}>
                            Allow
                        </button>
                        <button className="btn" onClick={() => void answer(false)}>
                            Cancel
                        </button>
                    </div>
                </Screen>
            );
        }
        default:
            return (
                <Screen title="Connecting to Lemma…">
                    <LoadingIndicator label={state === "reading" ? "Checking your session" : "Sending your answer"} />
                </Screen>
            );
    }
}
