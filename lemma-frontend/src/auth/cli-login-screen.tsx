"use client";

import { useEffect, useRef, useState } from "react";
import { LoadingIndicator } from "@/ui/loading";
import { Screen } from "./screens";
import { accountAccess, completionDestination, signedInEmail } from "./completion";
import {
    asksForCli,
    cliRequestFromSearch,
    deliverToCli,
    dropCliRequest,
    heldCliRequest,
    holdCliRequest,
    mintCliSession,
    type CliRequest,
} from "./cli-login";

const COMMAND = "lemma auth login";

type State = "reading" | "invalid" | "asking" | "handing" | "done" | "declined" | "failed";

/** `/auth/cli/login`: the browser's side of `lemma auth login`.
 *  `cli-login.ts` has the whole exchange. */
export function CliLogin() {
    const [state, setState] = useState<State>("reading");
    const [email, setEmail] = useState<string | null>(null);
    const [said, setSaid] = useState<string | null>(null);
    const request = useRef<CliRequest | null>(null);

    useEffect(() => {
        const search = window.location.search;
        /* A fresh link wins over a held one; a link that tried and failed does
           not fall back to an older request this tab happens to hold. */
        const found = asksForCli(search) ? cliRequestFromSearch(search) : heldCliRequest();
        if (!found) {
            dropCliRequest();
            setState("invalid");
            return;
        }
        request.current = found;
        holdCliRequest(found);
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
                const who = await signedInEmail();
                if (cancelled) return;
                setEmail(who);
                setState("asking");
            } catch (problem) {
                if (cancelled) return;
                setSaid(problem instanceof Error ? problem.message : null);
                setState("failed");
            }
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    const confirm = async () => {
        const current = request.current;
        if (!current) return;
        setState("handing");
        try {
            await deliverToCli(current, await mintCliSession());
            dropCliRequest();
            setState("done");
        } catch (problem) {
            dropCliRequest();
            setSaid(problem instanceof Error ? problem.message : null);
            setState("failed");
        }
    };

    const decline = () => {
        dropCliRequest();
        setState("declined");
    };

    switch (state) {
        case "invalid":
            return (
                <Screen
                    title="This sign-in link is incomplete"
                    lead={<>It does not say which terminal to hand the session to. Run <code>{COMMAND}</code> again and use the link it opens.</>}
                />
            );
        case "failed":
            return (
                <Screen
                    title="The CLI was not signed in"
                    lead={said ?? <>Something went wrong on the way. Run <code>{COMMAND}</code> again.</>}
                />
            );
        case "declined":
            return (
                <Screen
                    title="Nothing was signed in"
                    lead={<>The CLI was not given your account. If you did not run <code>{COMMAND}</code>, you can close this tab.</>}
                />
            );
        case "asking":
            return (
                <Screen
                    title={email ? "Sign in to the Lemma CLI as " + email + "?" : "Sign in to the Lemma CLI with this account?"}
                    lead={<>Only continue if you just ran <code>{COMMAND}</code> on this computer.</>}
                >
                    <div className="screen__actions">
                        <button className="btn btn--primary" onClick={() => void confirm()}>
                            Sign in the CLI
                        </button>
                        <button className="btn" onClick={decline}>
                            Cancel
                        </button>
                    </div>
                </Screen>
            );
        case "done":
            return <Screen title="The CLI is signed in" lead="Go back to your terminal. You can close this tab." />;
        default:
            return (
                <Screen title="Signing in the Lemma CLI…">
                    <LoadingIndicator label={state === "reading" ? "Checking your session" : "Handing your session to the CLI"} />
                </Screen>
            );
    }
}
