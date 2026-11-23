"use client";

import { useState, type ReactNode } from "react";
import { useMutation } from "@tanstack/react-query";
import { source } from "@/data";
import { ExternalIcon, RefreshIcon } from "@/ui/icons";
import { ConnectDialog } from "./connect-dialog";
import { consentLabel } from "./consent";
import { connectorProblem, type CatalogEntry, type Install } from "./install";
import { completionPath, hereWith, openAuthorization } from "./round-trip";
import { SignInLink } from "./sign-in-link";

/** A sign-in sent to a provider's page, waiting to come back. */
export interface SentSignIn {
    entryId: string;
    title: string;
    url: string;
    /** Whether a tab was opened for it. When the browser refused, the
     *  button that opens it is the way there. */
    opened: boolean;
}

export interface OrgConnect {
    /** Open the connect dialog for a service, against an install or none. */
    open: (entry: CatalogEntry, install: Install | null, ownAppFirst?: boolean) => void;
    /** Straight to the provider's sign-in on an install: reconnecting, or a
     *  server of the organization's own that nobody has signed in to. */
    signIn: (entry: CatalogEntry, installId: string | null) => void;
    /** Fetching the sign-in address for this service, and install if named. */
    starting: (entryId: string, installId?: string | null) => boolean;
    sent: SentSignIn | null;
    problem: { entryId: string; text: string } | null;
    /** Open the provider's page again, after the browser refused the tab. */
    again: () => void;
    /** "Done?": stop waiting, and read the lists again. */
    settle: () => void;
    /** The round trip came back: nothing is waiting any more. */
    heard: () => void;
    dismiss: () => void;
    /** The dialog, while one is open. */
    dialog: ReactNode;
}

/** Connecting an account to the organization, from anywhere on the
 *  Connectors page: the header's button, a card, a row's sheet, the band of
 *  what needs fixing.
 *
 *  One flow for the page rather than one per card, because they are one
 *  round trip — the provider's page in a tab of its own, the outcome heard
 *  on the page — and a card that kept its own could not be told when the
 *  sign-in it started came back. Opening the dialog rather than starting a
 *  sign-in: which of the two routes a connector is on is a question about
 *  its kinds, and the dialog is what reads them. */
export function useOrgConnect({ orgId, takenNames, mayInstall, onChanged }: {
    orgId: string;
    /** Every install name in the organization, for naming a new one. */
    takenNames: string[];
    mayInstall: boolean | null;
    onChanged: () => void;
}): OrgConnect {
    const [open, setOpen] = useState<{ entry: CatalogEntry; install: Install | null; ownAppFirst: boolean } | null>(null);
    const [sent, setSent] = useState<SentSignIn | null>(null);
    const [problem, setProblem] = useState<{ entryId: string; text: string } | null>(null);

    const start = useMutation({
        mutationFn: (input: { entry: CatalogEntry; installId: string | null; connectionFields?: Record<string, unknown> }) =>
            source.startAccount(
                orgId, input.entry.id, input.installId ?? undefined,
                /* Back to this page, whichever way the tab comes home. */
                completionPath(hereWith()),
                input.connectionFields,
            ),
        onSuccess: (started, input) => {
            setOpen(null);
            if (!started.authorizeUrl) {
                setSent(null);
                setProblem({ entryId: input.entry.id, text: "This one offered no way to sign in. It may need an app of your own first." });
            } else {
                /* Straight there, in a tab the completion page can report
                   back to. */
                setSent({
                    entryId: input.entry.id, title: input.entry.title, url: started.authorizeUrl,
                    opened: openAuthorization(started.authorizeUrl, input.entry.title),
                });
            }
            /* An install may have been made on the way. */
            onChanged();
        },
        onError: (failure, input) => setProblem({ entryId: input.entry.id, text: connectorProblem(failure, "That could not be started.") }),
    });

    const failure = open && problem?.entryId === open.entry.id ? problem.text : null;
    return {
        open: (entry, install, ownAppFirst = false) => { setProblem(null); setOpen({ entry, install, ownAppFirst }); },
        signIn: (entry, installId) => { setProblem(null); start.mutate({ entry, installId }); },
        starting: (entryId, installId) => start.isPending && start.variables?.entry.id === entryId
            && (installId === undefined || (start.variables?.installId ?? null) === installId),
        sent,
        problem,
        again: () => setSent((was) => was && { ...was, opened: openAuthorization(was.url, was.title) }),
        settle: () => { setSent(null); onChanged(); },
        heard: () => setSent(null),
        dismiss: () => setProblem(null),
        dialog: open && (
            <ConnectDialog
                orgId={orgId}
                connector={open.entry}
                install={open.install}
                takenNames={takenNames}
                mayInstall={mayInstall}
                authorizing={start.isPending}
                authorizeFailure={failure}
                ownAppFirst={open.ownAppFirst}
                onClose={() => setOpen(null)}
                onDone={() => { setOpen(null); onChanged(); }}
                onAuthorize={(installId, connectionFields) => start.mutate({ entry: open.entry, installId, connectionFields })}
            />
        ),
    };
}

/** Where a sign-in stands while it is out at the provider, said where the
 *  person is looking: the page's top, or the sheet of the service it is for.
 *  The link is always there too — an in-app browser may swallow the tab. */
export function SignInNote({ flow, entryId }: { flow: OrgConnect; entryId?: string }) {
    const sent = flow.sent && (!entryId || flow.sent.entryId === entryId) ? flow.sent : null;
    const problem = flow.problem && (!entryId || flow.problem.entryId === entryId) ? flow.problem : null;
    if (!sent && !problem) return null;
    return (
        <div className={"kconn-note" + (problem && !sent ? " kconn-note--bad" : "")} role="status">
            {problem && !sent && (
                <p className="kconn-note__t">
                    {problem.text}
                    <button type="button" className="b b--s b--quiet" onClick={flow.dismiss}>Dismiss</button>
                </p>
            )}
            {sent && (
                <>
                    <p className="kconn-note__t">
                        {sent.opened ? "Finish in the " + sent.title + " tab. This page hears when it is done." : "The browser didn’t open the sign-in."}
                        {!sent.opened && (
                            <button type="button" className="b b--s b--primary" onClick={flow.again}>
                                {consentLabel(sent.url, sent.title)} <ExternalIcon size={13} />
                            </button>
                        )}
                        <button type="button" className="b b--s b--quiet" onClick={flow.settle}>
                            <RefreshIcon size={14} /> Done?
                        </button>
                    </p>
                    <SignInLink url={sent.url} app={sent.title} opened={sent.opened} />
                </>
            )}
        </div>
    );
}
