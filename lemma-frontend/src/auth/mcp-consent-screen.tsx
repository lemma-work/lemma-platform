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
            const next = await answerConsentRequest(current, allow);
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
            const writes = request.scopes.includes(WRITE_SCOPE);
            return (
                <Screen
                    title={"Let " + request.client_name + " use " + request.pod_name + "?"}
                    lead={
                        <>
                            It will be able to read the tables and files in this space
                            {writes ? ", and add, change and delete them," : ""} as you
                            {email ? <> ({email})</> : null}. It can only see what you can see.
                        </>
                    }
                    footer={
                        <>
                            You will be sent back to <strong>{request.redirect_host}</strong>. Only continue if you
                            are connecting {request.client_name} yourself. You can disconnect it at any time.
                        </>
                    }
                >
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
