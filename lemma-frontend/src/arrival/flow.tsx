"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { source, type FirstWorkspace, type FirstWorkspaceWanted, type Org } from "@/data";
import { captureEvent } from "@/site/analytics/client";
import { ArrivalRow, ArrivalShell, LEMMA_IS, LemmaMark, OrgMark } from "./shell";
import { LemmasStep } from "./lemmas-step";
import { PluginsStep } from "./plugins-step";
import { WorkStep } from "./work-step";
import { nextStep, stepsFor, type ArrivalStepId } from "./steps";
import type { WorkAnswer } from "./work";

/** Where the way in left somebody. `startNew` asks for the New Lemma dialog
 *  on landing, from "Start a new one". */
export interface Landing {
    orgId: string | null;
    podId: string | null;
    startNew?: boolean;
}

/** Which way in this is.
 *
 *  - `first`: somebody with no organization yet. Their invitations are
 *    accepted, else their company's organization is offered, else one is
 *    made with a Lemma everyone in it shares -- all by the server's one rule.
 *  - `joined`: an invitation just accepted from Home, into `orgId`.
 *  - `work`: only the job question, for somebody who came in another way
 *    (a chat app, say) and has not been asked it yet. */
export type ArrivalStart =
    | { kind: "first"; invitations: number; suggested: Org[]; local: boolean }
    | { kind: "joined"; orgId: string; orgName: string; podId: string | null }
    | { kind: "work" };

export function ArrivalFlow({ start, asksWork, asksName = false, onEntered }: {
    start: ArrivalStart;
    /** Whether "What do you do?" is still ahead of them. */
    asksWork: boolean;
    /** The account has no name yet, so the job step asks for one too. */
    asksName?: boolean;
    onEntered: (landing: Landing) => void | Promise<void>;
}) {
    const [made, setMade] = useState<FirstWorkspace | null>(() =>
        start.kind === "joined"
            ? { orgId: start.orgId, orgName: start.orgName, entry: "invitation", orgCreated: false, podId: start.podId, podName: null, orgPodId: null, openToDomain: null }
            : null,
    );
    const [work, setWork] = useState<WorkAnswer | null>(null);
    /* Until the server says how they came in, the steps along the top are
       the likeliest ones: an invitation waiting, their company here, or new. */
    const entry = start.kind === "work"
        ? null
        : made?.entry ?? (start.kind === "first" && start.invitations > 0 ? "invitation" : start.kind === "first" && start.suggested.length > 0 ? "domain_join" : "new_org");
    const steps: ArrivalStepId[] =
        start.kind === "work"
            ? [...(asksWork ? (["work"] as const) : []), ...(PluginsStep ? (["plugins"] as const) : [])]
            : start.kind === "joined"
                ? ["lemmas", ...(asksWork ? (["work"] as const) : [])]
                : stepsFor(entry, { asksWork, hasPlugins: PluginsStep !== null });
    const [at, setAt] = useState<ArrivalStepId | null>(() => (start.kind === "joined" ? "lemmas" : start.kind === "work" ? "work" : "workspace"));

    /* Once: a second press while the frame reads the organization list
       again would land twice. */
    const left = useRef(false);
    const land = (podId?: string | null) => {
        if (left.current) return;
        left.current = true;
        void onEntered({ orgId: made?.orgId ?? null, podId: podId ?? made?.podId ?? null });
    };
    const advance = (from: ArrivalStepId) => {
        const next = nextStep(steps, from);
        if (next) setAt(next);
        else land();
    };

    const label = at === "work" ? "What you do" : at === "lemmas" ? "Lemmas to join" : "Your organization";
    return (
        <ArrivalShell steps={steps} at={at} label={label}>
            {at === "workspace" && start.kind === "first" && (
                <WorkspaceStep
                    start={start}
                    made={made}
                    onMade={(workspace) => {
                        setMade(workspace);
                        captureEvent("onboarding.arrived", { route: workspace.entry === "new_org" ? "new_org" : workspace.entry === "invitation" ? "invite" : "domain" });
                        /* Somebody who came into an organization that has people
                           goes straight to its Lemmas; a new one is shown first. */
                        if (workspace.entry !== "new_org") setAt("lemmas");
                    }}
                    onContinue={() => advance("workspace")}
                />
            )}
            {at === "lemmas" && made && (
                <LemmasStep
                    orgId={made.orgId}
                    orgName={made.orgName}
                    invitedPodId={made.entry === "invitation" ? made.podId : null}
                    onContinue={() => advance("lemmas")}
                    onStartNew={() => onEntered({ orgId: made.orgId, podId: null, startNew: true })}
                />
            )}
            {at === "work" && (
                <WorkStep
                    askName={asksName}
                    onDone={(answer) => {
                        setWork(answer);
                        advance("work");
                    }}
                />
            )}
            {at === "plugins" && PluginsStep && (
                <PluginsStep orgId={made?.orgId ?? ""} podId={made?.podId ?? null} work={work} onDone={(podId) => land(podId)} />
            )}
        </ArrivalShell>
    );
}

/** The organization: made, or their company's offered, or a problem.
 *
 *  Nothing is asked when there is nothing to decide -- an invitation is
 *  accepted and a new organization is made as soon as this is on screen,
 *  with the sentence about what a Lemma is to read while it happens. Their
 *  company's organization is a real choice (joining shows them to
 *  colleagues), so that one waits for them. */
function WorkspaceStep({ start, made, onMade, onContinue }: {
    start: Extract<ArrivalStart, { kind: "first" }>;
    made: FirstWorkspace | null;
    onMade: (workspace: FirstWorkspace) => void;
    onContinue: () => void;
}) {
    const company = start.invitations === 0 ? start.suggested[0] ?? null : null;
    const make = useMutation({
        /* Joining their company is the server's rule too, not a join made
           first: it lets in only the organization open to their domain, and
           puts them in its shared Lemma in the same request, as a newcomer. */
        mutationFn: async (wanted: { own?: boolean }) => {
            const asked: FirstWorkspaceWanted = {
                ...(wanted.own ? { ownOrganization: true } : {}),
                ...(start.local ? { team: false } : {}),
            };
            return source.ensureFirstWorkspace(asked);
        },
        onSuccess: onMade,
    });
    /* The organization's Lemmas, once it exists: the shared one is drawn
       with the face it will wear everywhere after, and the frame finds the
       list already read when they land. */
    const lemmas = useQuery({
        queryKey: ["pods", made?.orgId],
        queryFn: () => source.listPods(made?.orgId as string),
        enabled: Boolean(made?.orgId),
    });
    const begin = make.mutate;
    const begun = useRef(false);
    useEffect(() => {
        if (begun.current || company) return;
        begun.current = true;
        begin({});
    }, [company, begin]);

    if (made) {
        const shared = made.orgPodId ?? made.podId;
        const sharedFace = lemmas.data?.find((one) => one.id === shared)?.iconUrl ?? null;
        return (
            <>
                <h1 className="ar__t">{made.orgName} is ready</h1>
                <p className="ar__model">{LEMMA_IS}</p>
                <ul className="ar__rows">
                    <ArrivalRow
                        mark={<OrgMark />}
                        name={made.orgName}
                        line={made.openToDomain ? `Your organization. Anyone with an @${made.openToDomain} address can join.` : "Your organization. Invite people whenever you like."}
                    />
                    {shared && (
                        <ArrivalRow
                            mark={<LemmaMark id={shared} name={made.podName ?? made.orgName} iconUrl={sharedFace} />}
                            name={made.podName ?? made.orgName}
                            tag="Everyone"
                            line={`Its first Lemma. Everyone who joins ${made.orgName} is in it.`}
                        />
                    )}
                </ul>
                <div className="ar__acts">
                    <button type="button" className="ar__btn ar__btn--primary" autoFocus onClick={onContinue}>Continue</button>
                </div>
            </>
        );
    }

    if (company && !make.isPending && !make.isError) {
        return (
            <>
                <h1 className="ar__t">Your team is already here</h1>
                <p className="ar__model">{LEMMA_IS}</p>
                <ul className="ar__rows">
                    <ArrivalRow mark={<OrgMark />} name={company.name} line="Your email address lets you join it.">
                        <button type="button" className="ar__btn ar__btn--primary ar__btn--small" onClick={() => make.mutate({})}>Join</button>
                    </ArrivalRow>
                </ul>
                <p className="ar__note">
                    <button type="button" className="ar__quiet" onClick={() => make.mutate({ own: true })}>Start your own organization instead</button>
                </p>
            </>
        );
    }

    return (
        <>
            <h1 className="ar__t">{make.isError ? "Your organization isn’t set up yet" : "Setting up your organization…"}</h1>
            <p className="ar__model">{LEMMA_IS}</p>
            {make.isError ? (
                <>
                    <p className="ar__problem" role="alert">
                        {make.error instanceof Error && make.error.message ? make.error.message : "Something went wrong on the way."}
                    </p>
                    <div className="ar__acts">
                        <button type="button" className="ar__btn ar__btn--primary" onClick={() => make.mutate(make.variables ?? {})}>Try again</button>
                    </div>
                </>
            ) : (
                <p className="ar__note" role="status">This takes a moment.</p>
            )}
        </>
    );
}

/** The two reads the first way in needs before it can decide anything:
 *  invitations waiting, and organizations their email domain may join. */
export function useArrivalReads() {
    const invitations = useQuery({ queryKey: ["my-invitations"], queryFn: () => source.myInvitations() });
    const suggested = useQuery({ queryKey: ["suggested-orgs"], queryFn: () => source.suggestedOrgs() });
    return { invitations, suggested };
}
