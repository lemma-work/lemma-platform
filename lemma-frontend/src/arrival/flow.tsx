"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Mood } from "@/app/characters/loop/loop-puppet";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type FirstWorkspace, type Org } from "@/data";
import { lemma } from "@/session/client";
import { captureEvent } from "@/site/analytics/client";
import type { BuiltPlugin } from "@/site/plugins/catalog";
import { ArrivalRow, ArrivalShell, OrgMark } from "./shell";
import { NameStep } from "./name-step";
import { LemmaStep, type LemmaPart } from "./lemma-step";
import { AppsStep } from "./apps-step";
import { ReachStep, type ReachState } from "./reach-step";
import { ReadyStep } from "./ready-step";
import { useInstallRun } from "./install-run";
import { emptySlots, installSlots, pickedSlots, type StageSlot } from "./stage-model";
import type { StageModel } from "./stage";
import { nextStep, statusAt, stepsFor, type ArrivalStepId, type WorkspaceEntry } from "./steps";
import { firstWorkspaceWanted } from "./first-workspace";
import { workspaceNameFor } from "./workspace-name";

/** Where the way in left somebody. `startNew` asks for the New Lemma dialog
 *  on landing, from "Start a new Lemma"; `app` opens one of the Lemma's apps
 *  rather than the Lemma itself; `ask` starts a chat with the Lemma, asking
 *  it one of the first things the last step offered. */
export interface Landing {
    orgId: string | null;
    podId: string | null;
    startNew?: boolean;
    app?: string | null;
    ask?: string | null;
}

/** Which way in this is.
 *
 *  - `first`: somebody with no organization yet. Their invitations are
 *    accepted, else their company's organization is offered, else one is
 *    made with a Lemma everyone in it shares -- all by the server's one rule.
 *  - `joined`: an invitation just accepted from Home, into `orgId`.
 *  - `work`: only what they do and the apps after it, for somebody who came
 *    in another way (a chat app, say) and has not been asked yet. */
export type ArrivalStart =
    | { kind: "first"; invitations: number; suggested: Org[]; local: boolean }
    | { kind: "joined"; orgId: string; orgName: string; podId: string | null }
    | { kind: "work" };

/** The way in, as one living scene: Lemma asks your name; the Lemma made for
 *  you (or the one you came into) is born on the left and says hello by
 *  name; then, in its own voice, what you do and the apps for it -- each
 *  one ticked flies over and attaches to it -- where to reach it while they
 *  go in, and in. */
export function ArrivalFlow({ start, asksWork: asksNow, asksName = false, onEntered }: {
    start: ArrivalStart;
    /** Whether "What do you do?" is still ahead of them. */
    asksWork: boolean;
    /** The account has no name yet. */
    asksName?: boolean;
    onEntered: (landing: Landing) => void | Promise<void>;
}) {
    const queryClient = useQueryClient();
    /* Asked once is asked for the rest of this way in: saving the answer
       turns the question off, and its step stays in the count. */
    const [asksWork, setAsksWork] = useState(asksNow);
    if (asksNow && !asksWork) setAsksWork(true);
    const me = useQuery({ queryKey: ["current-user"], queryFn: () => lemma().users.current(), staleTime: 5 * 60_000 });
    const [name, setName] = useState<string | null>(null);
    const firstName = name ?? ((me.data as { first_name?: string | null } | undefined)?.first_name ?? "").trim();

    const [made, setMade] = useState<FirstWorkspace | null>(() =>
        start.kind === "joined"
            ? { orgId: start.orgId, orgName: start.orgName, entry: "invitation", orgCreated: false, podId: start.podId, podName: null, orgPodId: null, openToDomain: null }
            : null,
    );
    const [picked, setPicked] = useState<BuiltPlugin[]>([]);
    /* What the scene on the left holds, as the steps tell it. */
    const [stagePicks, setStagePicks] = useState<{ slug: string; landing?: boolean }[]>([]);
    const [reach, setReach] = useState<ReachState>({ on: [], heard: null });
    const [typing, setTyping] = useState(0);
    const [greeting, setGreeting] = useState(0);
    const [burst, setBurst] = useState(0);
    const [part, setPart] = useState<LemmaPart | null>(null);
    const [delighted, setDelighted] = useState(false);
    const delightTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
    const delight = useCallback((ms: number) => {
        setDelighted(true);
        clearTimeout(delightTimer.current);
        delightTimer.current = setTimeout(() => setDelighted(false), ms);
    }, []);
    useEffect(() => () => clearTimeout(delightTimer.current), []);

    /* Their company's organization is a real choice -- joining shows them to
       colleagues -- so it is asked before anything is made. Otherwise the
       organization and its Lemma are made while they type their name. */
    const company = start.kind === "first" && start.invitations === 0 ? start.suggested[0] ?? null : null;
    const make = useMutation({
        mutationFn: (wanted: { own?: boolean }) =>
            source.ensureFirstWorkspace(firstWorkspaceWanted({ own: wanted.own, local: start.kind === "first" && start.local })),
        onSuccess: (workspace) => {
            setMade(workspace);
            captureEvent("onboarding.arrived", { route: workspace.entry === "new_org" ? "new_org" : workspace.entry === "invitation" ? "invite" : "domain" });
        },
    });
    const begin = make.mutate;
    const begun = useRef(false);
    useEffect(() => {
        if (begun.current || start.kind !== "first" || company) return;
        begun.current = true;
        begin({});
    }, [start.kind, company, begin]);

    /* Somebody who came in another way already has an organization: the
       apps go into the Lemma they would land in there. */
    const orgs = useQuery({ queryKey: ["orgs"], queryFn: () => source.listOrgs(), enabled: start.kind === "work" });
    const workOrg = start.kind === "work" ? orgs.data?.[0]?.id ?? null : null;
    const workPods = useQuery({ queryKey: ["pods", workOrg], queryFn: () => source.listPods(workOrg as string), enabled: Boolean(workOrg) });
    const workPod = workPods.data?.find((one) => one.everyone) ?? workPods.data?.[0] ?? null;

    const orgId = made?.orgId ?? workOrg ?? "";
    /* The Lemma they land in: the one they were invited to, else the one
       everyone in the organization shares, else their own. */
    const podId = made?.podId ?? workPod?.id ?? null;
    const pods = useQuery({ queryKey: ["pods", orgId], queryFn: () => source.listPods(orgId), enabled: Boolean(orgId) });
    const pod = pods.data?.find((one) => one.id === podId) ?? null;
    const fresh = made?.entry === "new_org";
    const shared = made ? (fresh ? Boolean(made.orgPodId) : true) : Boolean(workPod?.everyone);

    /* A workspace of one the server could not name takes the name they give. */
    const named = useRef(false);
    useEffect(() => {
        if (named.current || !made?.orgCreated || !firstName) return;
        const wanted = workspaceNameFor(made.orgName, firstName);
        if (!wanted) return;
        named.current = true;
        void lemma().request("PATCH", `/organizations/${encodeURIComponent(made.orgId)}`, { body: { name: wanted } })
            .then(() => {
                setMade((was) => (was ? { ...was, orgName: wanted } : was));
                void queryClient.invalidateQueries({ queryKey: ["orgs"] });
            })
            .catch(() => undefined);
    }, [made, firstName, queryClient]);

    const steps: ArrivalStepId[] = start.kind === "work"
        ? [...(asksName ? (["name"] as const) : []), "apps", "reach", "ready"]
        : start.kind === "joined"
            ? stepsFor({ name: false, apps: asksWork })
            : stepsFor({ name: true, team: company !== null });
    const [at, setAt] = useState<ArrivalStepId>(() => steps[0]);

    /* Once: a second press while the frame reads the organization list
       again would land twice. */
    const left = useRef(false);
    const land = useCallback((where: { podId?: string | null; app?: string | null; ask?: string | null } = {}) => {
        if (left.current) return;
        left.current = true;
        void onEntered({ orgId: made?.orgId ?? workOrg ?? null, podId: where.podId ?? podId, app: where.app ?? null, ask: where.ask ?? null });
    }, [onEntered, made, workOrg, podId]);
    const advance = (from: ArrivalStepId) => {
        const next = nextStep(steps, from);
        if (next) setAt(next);
        else land();
    };

    /* The picks go in from the step that says where to reach the Lemma, and
       keep going on the one after. */
    const run = useInstallRun(pod, picked, at === "reach" || at === "ready");

    /* The scene on the left. The Lemma is born the moment it is shown: a
       burst of the logo's bars and a wave. Ready is the second burst. */
    const born = at !== "name" && at !== "team" && Boolean(pod);
    const wasBorn = useRef(born);
    useEffect(() => {
        if (born && !wasBorn.current) {
            setBurst((n) => n + 1);
            setGreeting((n) => n + 1);
            delight(2600);
        }
        wasBorn.current = born;
    }, [born, delight]);
    const readied = useRef(false);
    useEffect(() => {
        if (at !== "ready" || readied.current) return;
        readied.current = true;
        setBurst((n) => n + 1);
        setGreeting((n) => n + 1);
        delight(4000);
    }, [at, delight]);
    /* Your first hello, heard: it waves back. */
    useEffect(() => {
        if (!reach.heard) return;
        setGreeting((n) => n + 1);
        delight(3600);
    }, [reach.heard, delight]);
    /* Every app in: a nod of satisfaction. */
    useEffect(() => { if (run.done) delight(1500); }, [run.done, delight]);

    const slots: StageSlot[] = at === "lemma" ? emptySlots()
        : at === "apps" ? pickedSlots(stagePicks)
            : at === "reach" || at === "ready" ? installSlots(run.stage)
                : [];
    const mood: Mood = delighted ? "delighted" : at === "apps" ? "listening" : "idle";
    const stage: StageModel = {
        pod: born && pod ? { id: pod.id, name: pod.name, iconUrl: pod.iconUrl } : null,
        slots,
        you: born && firstName ? firstName.charAt(0).toUpperCase() : null,
        reach: at === "reach" || at === "ready" ? reach.on : [],
        mood,
        greeting,
        typing,
        heard: reach.heard,
        burst,
        part: at === "lemma" ? part : null,
    };
    const adding = run.stage.find((one) => one.kind === "adding");
    const status = reach.heard
        ? "Heard you"
        : at === "lemma" && !pod
            ? "Making your Lemma"
            : at === "reach"
                ? (adding
                    ? `Adding ${picked.find((one) => one.slug === adding.slug)?.name ?? "apps"}`
                    : reach.on.length > 0 ? "Listening for you"
                        : run.done ? "All in" : "Ready")
                : statusAt(at, { made: fresh });
    const frame = { steps, at, stage, status };
    const failed = make.isError ? (make.error instanceof Error ? make.error.message : "") : null;

    if (at === "name") {
        return (
            <ArrivalShell {...frame} label="Your name">
                <NameStep firstName={firstName} onType={() => setTyping((n) => n + 1)} onDone={(given) => { setName(given); advance("name"); }} />
            </ArrivalShell>
        );
    }

    if (at === "team" && company) {
        return (
            <ArrivalShell {...frame} label="Your team">
                <h1 className="ar__t">{firstName ? `${firstName}, your` : "Your"} team is already on Lemma.</h1>
                <p className="ar__model">Your email address lets you join {company.name}. You’ll meet its Lemmas next.</p>
                <ul className="ar__rows ar__rows--boxed">
                    <ArrivalRow mark={<OrgMark />} name={company.name} line="Your colleagues are here" />
                </ul>
                {make.isError && <p className="ar__problem" role="alert">{failed || "Something went wrong on the way."}</p>}
                <div className="ar__acts">
                    <span className="ar__sum">
                        <button type="button" className="ar__quiet" disabled={make.isPending} onClick={() => make.mutate({ own: true }, { onSuccess: () => setAt("lemma") })}>
                            Start your own instead
                        </button>
                    </span>
                    <button type="button" className="ar__btn ar__btn--primary" disabled={make.isPending} onClick={() => make.mutate({}, { onSuccess: () => setAt("lemma") })}>
                        {make.isPending ? "Joining…" : `Join ${company.name}`}
                    </button>
                </div>
            </ArrivalShell>
        );
    }

    if (at === "lemma") {
        return (
            <ArrivalShell {...frame} label="Your Lemma">
                <LemmaStep
                    name={firstName}
                    made={made}
                    failed={failed}
                    retry={() => make.mutate(make.variables ?? {})}
                    pod={pod}
                    onContinue={(renamed) => {
                        if (renamed && made) setMade({ ...made, orgName: renamed.org, podName: renamed.org });
                        advance("lemma");
                    }}
                    onStartNew={() => onEntered({ orgId: made?.orgId ?? null, podId: null, startNew: true })}
                    onPart={setPart}
                />
            </ArrivalShell>
        );
    }

    if (at === "apps") {
        return (
            <ArrivalShell {...frame} label="Apps">
                <AppsStep
                    name={firstName}
                    podId={podId}
                    lemmaName={pod?.name ?? made?.podName ?? "your Lemma"}
                    lemmaIcon={pod?.iconUrl ?? null}
                    shared={shared}
                    asksWork={asksWork}
                    onStage={setStagePicks}
                    onLanded={() => delight(900)}
                    onDone={(chosen) => {
                        setPicked(chosen);
                        /* A Lemma of their own can be written to with no apps
                           at all; somebody who came into another's has
                           nothing here to set up. */
                        if (chosen.length === 0 && !fresh) land();
                        else advance("apps");
                    }}
                />
            </ArrivalShell>
        );
    }

    if (at === "reach" && pod) {
        return (
            <ArrivalShell {...frame} label="Where to reach it">
                <ReachStep
                    pod={pod}
                    orgId={orgId}
                    team={shared}
                    firstName={firstName}
                    plugins={picked}
                    run={run}
                    onChange={setReach}
                    onDone={() => advance("reach")}
                />
            </ArrivalShell>
        );
    }

    if (at === "ready" && pod) {
        return (
            <ArrivalShell {...frame} label="Ready">
                <ReadyStep
                    pod={pod}
                    firstName={firstName}
                    plugins={picked}
                    run={run}
                    reach={reach.on}
                    shared={shared}
                    onOpen={(app) => land({ app })}
                    onAsk={(text) => land({ ask: text })}
                />
            </ArrivalShell>
        );
    }

    return (
        <ArrivalShell {...frame} label="Opening">
            <h1 className="ar__t">Opening your Lemma…</h1>
            <p className="ar__note" role="status">This takes a moment.</p>
        </ArrivalShell>
    );
}

/** The two reads the first way in needs before it can decide anything:
 *  invitations waiting, and organizations their email domain may join. */
export function useArrivalReads() {
    const invitations = useQuery({ queryKey: ["my-invitations"], queryFn: () => source.myInvitations() });
    const suggested = useQuery({ queryKey: ["suggested-orgs"], queryFn: () => source.suggestedOrgs() });
    return { invitations, suggested };
}

export type { WorkspaceEntry };
