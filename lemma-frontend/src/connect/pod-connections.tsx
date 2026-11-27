"use client";

import { useEffect, useId, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { askedLine, freshSlug, source, type Connector, type Pod, type PodConnectorMode } from "@/data";
import {
    botLogins, botReach, botSlug, connectedCount, connectionSaid, hasBotMode, missingLine, someoneMissing, twiceOver,
    type PodConnection, type PodConnections as Connections, type RowStatus,
} from "@/data/pod-connections";
import { isLandingPreview } from "@/marketing/preview-mode";
import { Calm, Problem } from "@/outcomes/calm";
import { PageActs } from "@/outcomes/page-acts";
import { TryAgain, codeOf } from "@/outcomes/read-again";
import { useMe } from "@/session/use-me";
import { markTint } from "@/shell/mark-tint";
import { ConfirmDialog } from "@/shell/confirm-dialog";
import { Modal } from "@/shell/modal";
import { AgentIcon, ChevronRightIcon, CloseIcon, PlusIcon, SearchIcon } from "@/ui/icons";
import { LoadingIndicator } from "@/ui/loading";
import { SkeletonRows } from "@/ui/skeleton";
import { addChoices, focusTarget, type AddChoice } from "./choices";
import { connectorProblem, type Install } from "./install";
import { useConnectFlow, type ConnectTarget } from "./pod-connect";
import { ServiceMark } from "./service-mark";
import { SignInLink } from "./sign-in-link";

/** `pod.connector.manage`: add, change and remove the pod's connectors, and
 *  take any account out of the pod. Never sharing one — that is its owner's. */
export const MANAGE_CONNECTORS = "pod.connector.manage";

type Flow = ReturnType<typeof useConnectFlow>;

/** Under the pod's connectors, so whatever reads those again — a connect, a
 *  switch on the organization's Connectors page — reads this again too. */
const connectionsKey = (podId: string) => ["pod-connectors", podId, "connections"] as const;

/** A Lemma's Connections: one row per app it uses, each saying whether it
 *  works for me and whose account it runs as, with the one button that
 *  helps — Connect, Use it here, Reconnect, or nothing where the fix is
 *  somebody else's (and then the row says whose). Everything else about an
 *  app is in its sheet: whose account it runs as, the sandbox switch once an
 *  account is shared, my account, who has connected, what it's for, and
 *  taking it out.
 *
 *  Read from one answer (`GET /pods/{pod}/connections`), judged by the
 *  server for whoever is asking, so the page cannot say "Connect" to someone
 *  whose account is already in use here.
 *
 *  `focus` is what a link named (`?connector=github`) — a slug, an
 *  installation or a catalog app. It picks out that app's row; an app the
 *  Lemma doesn't use yet gets a row of its own whose Connect adds it. */
export function PodConnections({ pod, focus }: { pod: Pod; focus: string | null }) {
    const me = useMe() ?? "";
    const cache = useQueryClient();
    const preview = isLandingPreview();
    const read = useQuery({ queryKey: connectionsKey(pod.id), queryFn: () => source.podConnections(pod.id), staleTime: 30_000 });
    const catalog = useQuery({ queryKey: ["connectors"], queryFn: () => source.listConnectors(), staleTime: 10 * 60_000 });
    /* Under the key the organization's Connectors page keeps them by. A member
       may be refused them, and still connects against what the row names. */
    const installs = useQuery({ queryKey: ["installs", pod.orgId], queryFn: () => source.listInstallations(pod.orgId), staleTime: 60_000, retry: false });
    const titles = useMemo(() => new Map((catalog.data ?? []).map((one) => [one.id, one])), [catalog.data]);
    const flow = useConnectFlow(pod, titles, installs.data);
    const [openSlug, setOpenSlug] = useState<string | null>(null);
    const [adding, setAdding] = useState(false);
    const [sharing, setSharing] = useState<PodConnection | null>(null);
    /* The app just added, picked out as if a link had named it. */
    const [added, setAdded] = useState<string | null>(null);

    const connections = read.data;
    const items = useMemo(() => connections?.items ?? [], [connections]);
    const may = !preview && (connections?.mayManage ?? false);
    const named = useMemo(() => twiceOver(items), [items]);
    const usableInstalls = (installs.data ?? []).filter((one) => one.status !== "DISABLED");
    /* Judged only once the lists it is judged against are in: until then a
       catalog id reads as a name that matches nothing. */
    const settled = read.isSuccess && !catalog.isPending && !installs.isPending;
    const target = settled ? focusTarget(focus, items, usableInstalls, catalog.data ?? []) : { kind: "none" as const };
    const focused = target.kind === "connector" ? target.slug : added;
    const opened = openSlug ? items.find((one) => one.slug === openSlug) ?? null : null;

    /* Who each per-member app still waits on, by name, for the band and the
       rows: asked only of the apps an editor's count says someone is
       missing from. */
    const counted = may ? items.filter(someoneMissing) : [];
    const coverages = useQueries({
        queries: counted.map((item) => ({
            queryKey: ["pod-connectors", pod.id, "coverage", item.slug],
            queryFn: () => source.podConnectorCoverage(pod.id, item.slug),
            staleTime: 30_000,
            retry: false,
        })),
    });
    const waitingOn = new Map(counted.map((item, index) => [
        item.slug,
        (coverages[index]?.data?.waitingOn ?? []).filter((one) => one.userId !== me),
    ]));

    const refresh = () => {
        void cache.invalidateQueries({ queryKey: ["pod-connectors", pod.id] });
        void cache.invalidateQueries({ queryKey: ["pod-accounts", pod.id] });
        void cache.invalidateQueries({ queryKey: ["accounts", pod.orgId] });
    };
    const band = items.filter((item) => (waitingOn.get(item.slug) ?? []).length > 0);

    return (
        <div className="kpc">
            {may && (
                <PageActs className="kpc-acts">
                    <button type="button" className="b b--m b--primary" disabled={!read.isSuccess} onClick={() => setAdding(true)}>
                        <PlusIcon size={16} /> Add an app
                    </button>
                </PageActs>
            )}

            {read.isPending && (
                <SkeletonRows label={"Reading " + pod.name + "’s connections"} rows={4}
                    slow={"Still reading " + pod.name + "’s connections. It’s slower than usual."} />
            )}
            {read.isError && (
                <Calm tone="warn" title={"Couldn’t read " + pod.name + "’s connections"} note={codeOf(read.error)}
                    actions={<TryAgain busy={read.isFetching} onClick={() => void read.refetch()} />}>
                    Nothing in them changed. {pod.name} still works through the apps it was given.
                </Calm>
            )}

            {connections && (
                <>
                    {band.length > 0 && (
                        <div className="kneed" role="region" aria-label="Needs you">
                            {band.map((item) => (
                                <WaitingRow key={item.slug} pod={pod} item={item} people={waitingOn.get(item.slug) ?? []} />
                            ))}
                        </div>
                    )}

                    {target.kind === "connect" && focus && (
                        <AddItRow pod={pod} name={focus} title={target.title} entry={titles.get(target.connectorId) ?? null}
                            connect={{ slug: focus, connectorId: target.connectorId, authConfigId: target.installId }} flow={flow} />
                    )}
                    {target.kind === "unknown" && (
                        <p className="kpc-quiet" role="status">{pod.name} doesn’t use anything called {focus}.</p>
                    )}

                    {items.length > 0 ? (
                        <div className="klist">
                            {items.map((item) => (
                                <ConnectionRow
                                    key={item.slug}
                                    pod={pod}
                                    item={item}
                                    me={me}
                                    may={may}
                                    named={named.has(item.connectorId)}
                                    waiting={waitingOn.get(item.slug) ?? []}
                                    focused={focused === item.slug}
                                    flow={flow}
                                    onOpen={() => setOpenSlug(item.slug)}
                                    onShareMine={() => setSharing(item)}
                                    onChanged={refresh}
                                />
                            ))}
                        </div>
                    ) : target.kind !== "connect" && (
                        <Calm icon="plug" title={pod.name + " doesn’t use any apps yet"}
                            actions={may ? <button type="button" className="b b--m b--primary" onClick={() => setAdding(true)}><PlusIcon size={16} /> Add an app</button> : undefined}>
                            {may ? "Add one, and you’re signing in. Others connect theirs the first time " + pod.name + " needs it."
                                : "Someone who can change " + pod.name + " adds them."}
                        </Calm>
                    )}
                </>
            )}

            {opened && connections && (
                <ConnectionSheet
                    pod={pod}
                    item={opened}
                    items={items}
                    me={me}
                    may={may}
                    named={named.has(opened.connectorId)}
                    bot={connections.githubBot}
                    flow={flow}
                    onShareMine={() => setSharing(opened)}
                    onChanged={refresh}
                    onClose={() => setOpenSlug(null)}
                />
            )}
            {sharing && (
                <ShareMine pod={pod} item={sharing} me={me} onClose={() => setSharing(null)}
                    onDone={() => { setSharing(null); refresh(); }} />
            )}
            {adding && connections && (
                <AddApp
                    pod={pod}
                    connections={connections}
                    catalog={catalog.data ?? []}
                    installs={usableInstalls}
                    titleOf={(id) => titles.get(id)?.title ?? id}
                    flow={flow}
                    onClose={() => setAdding(false)}
                    onAdded={(slug) => { setAdding(false); setAdded(slug); refresh(); }}
                />
            )}
            {flow.element}
        </div>
    );
}

/** A dot and a word. */
function Status({ status }: { status: RowStatus }) {
    const tone = status.tone === "ok" ? "" : " kstatus--" + status.tone;
    return <span className={"kstatus" + tone}>{status.word}</span>;
}

/** An app's mark: its logo in the kit's circle, or for the bot, the Lemma's
 *  own tint with a robot on it — the Lemma acting, not a person. */
function AppMark({ pod, item, entry, large = false }: { pod: Pod; item: Pick<PodConnection, "mode" | "title" | "icon">; entry?: Connector | null; large?: boolean }) {
    if (item.mode === "APP") {
        return (
            <span className={"kmark" + (large ? " kmark--lg" : "")} style={markTint(pod.id)} aria-hidden="true">
                <AgentIcon size={large ? 26 : 20} />
            </span>
        );
    }
    return <ServiceMark entry={{ title: item.title, icon: item.icon || entry?.icon || "" }} large={large} />;
}

/** Where a sign-in for this app stands: being fetched, out in another tab,
 *  a link where no tab opened, or why it didn't start. */
function SignInState({ slug, app, flow, onDone }: { slug: string; app: string; flow: Flow; onDone: () => void }) {
    return (
        <>
            {flow.waiting === slug && (
                <span className="kpc-wait" role="status">
                    Finish signing in to {app} in the other tab…
                    <button type="button" className="b b--s b--secondary" onClick={onDone}>I’m done</button>
                </span>
            )}
            {flow.link?.slug === slug && flow.waiting !== slug && <SignInLink url={flow.link.url} app={flow.link.app} opened={flow.link.opened} />}
            {flow.problem?.slug === slug && <span className="kpc-problem" role="alert">{flow.problem.text}</span>}
        </>
    );
}

/** One app: its mark and name, what it runs as for me (or why it can't), and
 *  the one thing I can do about it. The row opens its sheet. */
function ConnectionRow({ pod, item, me, may, named, waiting, focused, flow, onOpen, onShareMine, onChanged }: {
    pod: Pod;
    item: PodConnection;
    me: string;
    may: boolean;
    /** The Lemma has two of this app: its slug tells them apart. */
    named: boolean;
    /** Others it waits on, by an editor's count. */
    waiting: { name: string }[];
    focused: boolean;
    flow: Flow;
    onOpen: () => void;
    onShareMine: () => void;
    onChanged: () => void;
}) {
    const row = useRef<HTMLDivElement>(null);
    useEffect(() => { if (focused) row.current?.scrollIntoView({ block: "center" }); }, [focused]);
    const useHere = useUseHere(pod, item, onChanged);
    const said = connectionSaid(item, pod.name, me);
    const standing = item.me.standing;
    const busy = flow.busy === item.slug;
    const out = flow.waiting === item.slug;
    /* My standing first: when it asks something of me, that is the row's
       one thing. Otherwise an editor sees who it still waits on. */
    const mine = standing === "connect" || standing === "reconnect" || standing === "use_existing" || standing === "install";
    const count = may && !mine && standing !== "not_allowed" && item.mode === "PER_MEMBER" && item.status === "READY" ? connectedCount(item.coverage) : "";
    const status: RowStatus = !mine && waiting.length > 0
        ? { word: "Waiting on " + (waiting.length === 1 ? waiting[0].name : waiting.length + " people"), tone: "warn" }
        : said.status;
    const ownable = Boolean(item.me.account || item.me.existing);
    const sharedWaiting = item.mode === "SHARED" && (standing === "waiting" || item.status === "NEEDS_ACCOUNT") && item.status !== "UNAVAILABLE";

    return (
        <div ref={row} className="krow krow--open kpc-row" data-focused={focused || undefined}
            onClick={(event) => { if (!(event.target as HTMLElement).closest("button, a, input")) onOpen(); }}>
            <AppMark pod={pod} item={item} />
            <span className="kpc-main">
                <span className="kpc-name">
                    <button type="button" className="kpc-open" aria-haspopup="dialog" onClick={onOpen}>{item.title}</button>
                    {named && <span className="ktag kpc-slug">{item.slug}</span>}
                </span>
                <span className="kpc-line">{said.line}</span>
                <SignInState slug={item.slug} app={item.title} flow={flow} onDone={flow.refresh} />
                {useHere.isError && <span className="kpc-problem" role="alert">{connectorProblem(useHere.error, "It couldn’t be added here just now.")}</span>}
            </span>
            <span className="kpc-side">
                {count && <span className="kpc-who">{count}</span>}
                {standing === "use_existing" ? (
                    <>
                        <span className="kpc-offer">You’ve connected {item.title} already.</span>
                        <button type="button" className="b b--s b--primary" disabled={useHere.isPending} onClick={() => useHere.mutate()}>
                            {useHere.isPending ? "Adding…" : "Use it here"}
                        </button>
                    </>
                ) : (standing === "connect" || standing === "reconnect") && !out ? (
                    <>
                        <Status status={said.status} />
                        <button type="button" className="b b--s b--primary" disabled={flow.pending}
                            onClick={() => flow.connect(target(item), standing === "reconnect")}>
                            {busy ? <LoadingIndicator inline label="Opening sign-in" /> : standing === "reconnect" ? "Reconnect" : "Connect"}
                        </button>
                    </>
                ) : standing === "install" && !out ? (
                    <>
                        <Status status={said.status} />
                        <GithubFix pod={pod} item={item} onChanged={onChanged} />
                    </>
                ) : sharedWaiting && may && ownable ? (
                    <>
                        <Status status={said.status} />
                        <button type="button" className="b b--s b--primary" onClick={onShareMine}>Use mine</button>
                    </>
                ) : !out && <Status status={status} />}
                <ChevronRightIcon size={16} className="kpc-chev" aria-hidden="true" />
            </span>
        </div>
    );
}

/** The one way on for a GitHub account that reaches nothing here yet:
 *  install the App, pick which installation, or open the installation on
 *  GitHub for whoever has to act — and, after any of them, check again,
 *  which asks GitHub past what was last heard. */
function GithubFix({ pod, item, onChanged }: { pod: Pod; item: PodConnection; onChanged: () => void }) {
    const github = item.me.github;
    const accountId = item.me.account?.id ?? "";
    // Asking GitHub again takes the account, which only its owner has here.
    // Anyone else can only re-read the page, which helps when GitHub was slow.
    const canCheck = Boolean(accountId) || github?.wait === "check";
    const check = useMutation({
        mutationFn: async () => { if (accountId) await source.checkGithubAccount(pod.orgId, accountId); },
        onSettled: onChanged,
    });
    const pick = useMutation({
        mutationFn: (installationId: string) => source.pickGithubInstallation(pod.orgId, accountId, installationId),
        onSuccess: onChanged,
    });
    const busy = check.isPending || pick.isPending;
    const link = github?.wait === "install" ? github.installUrl : github?.manageUrl ?? "";
    return (
        <span className="kpc-fix">
            {github?.wait === "choose" && accountId ? github.choices.slice(0, 3).map((one) => (
                <button key={one.installationId} type="button" className="b b--s b--primary" disabled={busy}
                    onClick={() => pick.mutate(one.installationId)}>
                    {pick.isPending && pick.variables === one.installationId ? "Using…" : "Use " + (one.login || "this one")}
                </button>
            )) : link ? (
                <a className={"b b--s" + (github?.wait === "install" ? " b--primary" : "")} href={link} target="_blank" rel="noreferrer">
                    {github?.wait === "install" ? "Install on GitHub" : "Open on GitHub"}
                </a>
            ) : null}
            {canCheck && (
                <button type="button" className="b b--s" disabled={busy} onClick={() => check.mutate()}>
                    {check.isPending ? "Checking…" : "Check again"}
                </button>
            )}
            {(check.isError || pick.isError) && (
                <span className="kpc-problem" role="alert">{connectorProblem(check.error ?? pick.error, "GitHub didn’t answer just now. Try again in a moment.")}</span>
            )}
        </span>
    );
}

/** What a sign-in is started for, from a row. */
function target(item: PodConnection): ConnectTarget {
    return { slug: item.slug, connectorId: item.connectorId, authConfigId: item.authConfigId, installationName: item.installationName };
}

/** "Use it here": an account of mine the organization already has, put in
 *  this Lemma — no sign-in. The server makes it my own here, and declares
 *  the app if it has to. */
function useUseHere(pod: Pod, item: PodConnection, onChanged: () => void) {
    return useMutation({
        mutationFn: () => source.addPodAccount(pod.id, item.me.existing?.accountId ?? ""),
        onSuccess: onChanged,
    });
}

/** An app a link named that the Lemma doesn't use yet. Its Connect is the
 *  ordinary sign-in, started for this Lemma, which the server adds the app
 *  to when it finishes. */
function AddItRow({ pod, name, title, entry, connect, flow }: {
    pod: Pod;
    name: string;
    title: string;
    entry: Connector | null;
    connect: ConnectTarget;
    flow: Flow;
}) {
    const row = useRef<HTMLDivElement>(null);
    useEffect(() => { row.current?.scrollIntoView({ block: "center" }); }, []);
    return (
        <div className="klist">
            <div ref={row} className="krow kpc-row" data-focused role="region" aria-label={"Connect " + title}>
                <ServiceMark entry={{ title, icon: entry?.icon ?? "" }} />
                <span className="kpc-main">
                    <span className="kpc-name"><b>{title}</b></span>
                    <span className="kpc-line">{pod.name} doesn’t use {title} yet. Connect your account and it will, as each person’s own.</span>
                    <SignInState slug={name} app={title} flow={flow} onDone={flow.refresh} />
                </span>
                <span className="kpc-side">
                    {flow.waiting !== name && (
                        <button type="button" className="b b--s b--primary" disabled={flow.pending} onClick={() => flow.connect(connect)}>
                            {flow.busy === name ? <LoadingIndicator inline label="Opening sign-in" /> : "Connect"}
                        </button>
                    )}
                </span>
            </div>
        </div>
    );
}

/** One line of the band: who hasn't connected an app the Lemma uses as each
 *  person's own, and asking them. Asking is once a day per person on the
 *  server's side, so asking again only says who was asked already. */
function WaitingRow({ pod, item, people }: { pod: Pod; item: PodConnection; people: { userId: string; name: string }[] }) {
    const ask = useMutation({ mutationFn: () => source.askMembersToConnect(pod.id, item.slug, people.map((one) => one.userId)) });
    return (
        <div className="krow kpc-need">
            <span className="kpc-line kpc-line--ink">
                {missingLine(people.map((one) => one.name), item.title, pod.name)}
                {ask.data && <span className="kpc-said" role="status"> {askedLine(ask.data)}</span>}
                {ask.isError && <span className="kpc-problem" role="alert"> {connectorProblem(ask.error, "They couldn’t be asked just now.")}</span>}
            </span>
            {!ask.data && (
                <button type="button" className="b b--s b--secondary" disabled={ask.isPending} onClick={() => ask.mutate()}>
                    {ask.isPending ? "Asking…" : people.length === 1 ? "Ask " + people[0].name : "Ask them"}
                </button>
            )}
        </div>
    );
}

/* ── the sheet ──────────────────────────────────────────────────────── */

/** Focus moves in, the keyboard stays in while it is open, Escape closes it,
 *  and focus goes back where it was. A dialog over it answers its own keys. */
function useSheetKeys(panel: RefObject<HTMLElement | null>, onClose: () => void) {
    const close = useRef(onClose);
    close.current = onClose;
    useEffect(() => {
        const previous = document.activeElement as HTMLElement | null;
        panel.current?.querySelector<HTMLElement>(".ksheet__x")?.focus();
        const onKey = (event: KeyboardEvent) => {
            if (event.defaultPrevented || document.querySelector(".modal")) return;
            if (event.key === "Escape") { event.preventDefault(); close.current(); return; }
            if (event.key !== "Tab" || !panel.current) return;
            const focusable = Array.from(panel.current.querySelectorAll<HTMLElement>("button:not([disabled]), a[href], input:not([disabled]), textarea"))
                .filter((el) => el.getClientRects().length > 0);
            const first = focusable[0], last = focusable.at(-1);
            if (!first) { event.preventDefault(); return; }
            if (!panel.current.contains(document.activeElement)) { event.preventDefault(); first.focus(); return; }
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        };
        document.addEventListener("keydown", onKey);
        return () => {
            document.removeEventListener("keydown", onKey);
            if (previous?.isConnected) previous.focus();
        };
    }, [panel]);
}

/** One app in this Lemma, from the side, in the order people need it: whose
 *  account it runs as, the sandbox switch once it's mine for everyone, my
 *  account, who has connected, what it's for, and taking it out. */
function ConnectionSheet({ pod, item, items, me, may, named, bot, flow, onShareMine, onChanged, onClose }: {
    pod: Pod;
    item: PodConnection;
    items: PodConnection[];
    me: string;
    may: boolean;
    named: boolean;
    bot: Connections["githubBot"];
    flow: Flow;
    onShareMine: () => void;
    onChanged: () => void;
    onClose: () => void;
}) {
    const panel = useRef<HTMLElement>(null);
    const titleId = useId();
    useSheetKeys(panel, onClose);
    const cache = useQueryClient();
    const app = item.title;
    const mineShared = item.mode === "SHARED" && item.runsAs.ownerUserId !== "" && item.runsAs.ownerUserId === me;
    /* My pod account that runs it for everyone: the sandbox switch is on it,
       and switching back to each person's own takes it back to mine alone. */
    const podAccounts = useQuery({
        queryKey: ["pod-accounts", pod.id],
        queryFn: () => source.listPodAccounts(pod.id),
        enabled: mineShared,
        staleTime: 30_000,
    });
    const sharedMine = mineShared
        ? (podAccounts.data ?? []).find((one) => one.ownerId === me && one.usedBy.includes(item.slug))
            ?? (podAccounts.data ?? []).find((one) => one.ownerId === me && one.id === item.me.account?.podAccountId)
            ?? null
        : null;

    const change = useMutation({
        mutationFn: async (mode: PodConnectorMode) => {
            await source.changePodConnector(pod.id, item.slug, {
                mode,
                ...(mode !== "SHARED" ? { sharedPodAccountId: "" } : {}),
                /* A connection kept to some people (made before this page, or
                   imported) becomes the whole Lemma's with the bot, in the
                   same request: the server takes the bot only that way. */
                ...(mode === "APP" ? { visibility: "POD" as const } : {}),
            });
            /* An account shared with nothing using it is not left behind. */
            if (sharedMine) await source.changePodAccount(pod.id, sharedMine.id, { visibility: "PERSONAL" }).catch(() => undefined);
        },
        onSuccess: onChanged,
    });
    const sandbox = useMutation({
        mutationFn: (on: boolean) => source.changePodAccount(pod.id, sharedMine?.id ?? "", { sandboxes: on }),
        onSuccess: (saved) => {
            /* Drawn as saved at once, so the switch does not spring back
               until the list is read again. */
            if (saved) cache.setQueryData<typeof podAccounts.data>(["pod-accounts", pod.id], (was) => was?.map((one) => (one.id === saved.id ? saved : one)));
            onChanged();
        },
    });
    const useHere = useUseHere(pod, item, onChanged);
    const [removing, setRemoving] = useState<"app" | "mine" | null>(null);
    const drop = useMutation({
        mutationFn: () => removing === "app"
            ? source.removePodConnector(pod.id, item.slug)
            : source.removePodAccount(pod.id, item.me.account?.podAccountId ?? ""),
        onSuccess: () => {
            const gone = removing === "app";
            setRemoving(null);
            onChanged();
            if (gone) onClose();
        },
    });

    const label = item.me.account?.label || item.me.existing?.label || "";
    /* Offered where it could be the Lemma's one bot: this connector already
       is, or the organization has the App and no other connector here acts
       as it (the server refuses a second, as the add dialog never offers one). */
    const botHere = hasBotMode(item.connectorId)
        && (item.mode === "APP" || (bot !== null && !items.some((one) => one.mode === "APP" && one.slug !== item.slug)));
    const ownGithub = items.some((one) => one.connectorId === item.connectorId && one.mode === "PER_MEMBER");
    const otherShared = item.mode === "SHARED" && !mineShared && item.runsAs.kind !== "nobody";
    const ownerName = item.runsAs.ownerName || "Someone";
    const pick = (mode: PodConnectorMode | "MINE") => {
        if (change.isPending) return;
        if (mode === "MINE") { onShareMine(); return; }
        if (mode !== item.mode) change.mutate(mode);
    };
    const said = connectionSaid(item, pod.name, me);

    /* Over the whole window, above the frame's sidebar, as every sheet is. */
    return createPortal(
        <>
            <div className="ksheet-veil" aria-hidden="true" onMouseDown={() => { if (!document.querySelector(".modal")) onClose(); }} />
            <aside className="ksheet kpc-sheet" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={panel}>
                <header className="ksheet__head">
                    <AppMark pod={pod} item={item} large />
                    <h2 id={titleId}>{app} in {pod.name}</h2>
                    <p>{said.line}</p>
                    {named && <p>Apps and agents call it <span className="kpc-fig">{item.slug}</span>.</p>}
                    <button type="button" className="ksheet__x b b--m b--quiet b--icon" aria-label="Close" onClick={onClose}>
                        <CloseIcon size={18} />
                    </button>
                </header>

                <div className="ksheet__body">
                    <SignInState slug={item.slug} app={app} flow={flow} onDone={flow.refresh} />

                    <section className="kpc-part" aria-label="Runs as">
                        <h3 className="kpc-part__h">Runs as</h3>
                        {may ? (
                            <div className="kopts" role="radiogroup" aria-label={"Whose account " + app + " runs as"}>
                                <Choice name={item.slug} checked={item.mode === "PER_MEMBER"} disabled={change.isPending} onPick={() => pick("PER_MEMBER")}
                                    title="Each person’s own account">
                                    Whoever asks, or the person an agent works for. People connect their own the first time {pod.name} needs it.
                                </Choice>
                                {otherShared && (
                                    <Choice name={item.slug} checked disabled={change.isPending} onPick={() => undefined} title={ownerName + "’s, for everyone"}>
                                        Every call in {pod.name} runs as {item.runsAs.label ? <span className="kpc-fig">{item.runsAs.label}</span> : ownerName + "’s account"}. Only {ownerName} could turn this on.
                                    </Choice>
                                )}
                                <Choice name={item.slug} checked={mineShared}
                                    disabled={change.isPending || (!item.me.account && !item.me.existing)} onPick={() => pick("MINE")} title="Mine, for everyone">
                                    {item.me.account || item.me.existing
                                        ? <>Every call in {pod.name} runs as {label ? <span className="kpc-fig">{label}</span> : "your account"}, including schedules nobody is watching. Only you can turn this on.</>
                                        : <>Connect your own {app} here first. Only you can put your account in front of everyone.</>}
                                </Choice>
                                {botHere && (
                                    <Choice name={item.slug} checked={item.mode === "APP"} disabled={change.isPending || bot === null} onPick={() => pick("APP")} title={pod.name + "’s bot"}>
                                        {bot
                                            ? <>Every call runs as {botLogins(bot)}’s GitHub App, reads included. Nobody connects or shares an account, and it keeps working when people leave. {botReach(bot)}</>
                                            : <>Waiting for someone who manages the organization’s apps to install the GitHub App.</>}
                                    </Choice>
                                )}
                                {change.isError && <Problem onRetry={() => change.mutate(change.variables ?? item.mode)} retrying={change.isPending}>
                                    {connectorProblem(change.error, "It couldn’t be changed just now.")}
                                </Problem>}
                            </div>
                        ) : (
                            <p className="kpc-quiet">
                                {item.mode === "APP" ? pod.name + "’s bot, the organization’s GitHub App."
                                    : item.mode === "SHARED" ? (mineShared ? "Your account" : ownerName + "’s account") + ", for everyone."
                                        : "Each person’s own account."}{" "}
                                Someone who manages {pod.name} can change that.
                            </p>
                        )}
                        {mineShared && sharedMine && sharedMine.sandboxes !== null && (
                            <div className="kpc-kv">
                                <span className="kpc-kv__l">
                                    <span>Allow it inside sandboxes<small>Agents can use your token for git and gh. Anything run in that sandbox afterwards can read it.</small></span>
                                </span>
                                <button type="button" role="switch" className="kswitch" aria-label="Allow inside sandboxes"
                                    aria-checked={sandbox.isPending ? sandbox.variables : sharedMine.sandboxes} disabled={sandbox.isPending}
                                    onClick={() => sandbox.mutate(!sharedMine.sandboxes)} />
                            </div>
                        )}
                        {sandbox.data?.notice && <p className="kpc-quiet" role="status">{sandbox.data.notice}</p>}
                        {sandbox.isError && <Problem>{connectorProblem(sandbox.error, "It couldn’t be changed just now.")}</Problem>}
                    </section>

                    {item.mode === "APP" ? (
                        <>
                            <section className="kpc-part" aria-label="The bot">
                                <h3 className="kpc-part__h">The bot</h3>
                                <div className="kpc-kv">
                                    <span className="kpc-kv__l">
                                        <AppMark pod={pod} item={item} />
                                        <span>{bot ? botLogins(bot) + "’s GitHub App" : "The organization’s GitHub App"}
                                            <small>{bot ? "Every call shows as the bot on GitHub, and as the person who asked in Lemma’s history. " + botReach(bot)
                                                : "Not installed on a GitHub organization of yours yet."}</small>
                                        </span>
                                    </span>
                                </div>
                            </section>
                            {!ownGithub && (
                                <section className="kpc-part" aria-label="To act as yourself">
                                    <h3 className="kpc-part__h">To act as yourself</h3>
                                    <div className="kpc-kv">
                                        <span className="kpc-kv__l">
                                            <ServiceMark entry={{ title: app, icon: item.icon }} />
                                            <span>Your own {app}<small>A few things only a person can do on {app}, like reading your gists. Connect yours for those.</small></span>
                                        </span>
                                        <button type="button" className="b b--s b--secondary" disabled={flow.pending}
                                            onClick={() => flow.connect({ slug: freshSlug(item.connectorId, items.map((one) => one.slug)), connectorId: item.connectorId, authConfigId: item.authConfigId })}>
                                            Connect mine
                                        </button>
                                    </div>
                                </section>
                            )}
                        </>
                    ) : (
                        <section className="kpc-part" aria-label="Your account">
                            <h3 className="kpc-part__h">Your account</h3>
                            <div className="kpc-kv">
                                <span className="kpc-kv__l">
                                    <ServiceMark entry={{ title: app, icon: item.icon }} />
                                    {item.me.account ? (
                                        <span><span className="kpc-fig">{item.me.account.label || "Your " + app + " account"}</span>
                                            <small>{item.me.standing === "reconnect" ? app + " signed you out." : mineShared ? "Runs " + app + " for everyone here." : item.mode === "SHARED" ? "Only your own calls use it while it runs as someone else’s." : "Used when you, or an agent working for you, use " + app + " here."}</small>
                                        </span>
                                    ) : item.me.existing ? (
                                        <span><span className="kpc-fig">{item.me.existing.label || "Your " + app + " account"}</span><small>Connected in your organization, not used here yet.</small></span>
                                    ) : (
                                        <span>Not connected<small>{item.me.standing === "not_allowed" ? "You aren’t allowed to use " + app + " here." : "Connect yours to use " + app + " in " + pod.name + "."}</small></span>
                                    )}
                                </span>
                                {item.me.standing === "use_existing" ? (
                                    <button type="button" className="b b--s b--primary" disabled={useHere.isPending} onClick={() => useHere.mutate()}>{useHere.isPending ? "Adding…" : "Use it here"}</button>
                                ) : item.me.standing === "connect" || item.me.standing === "reconnect" ? (
                                    <button type="button" className="b b--s b--primary" disabled={flow.pending} onClick={() => flow.connect(target(item), item.me.standing === "reconnect")}>
                                        {flow.busy === item.slug ? <LoadingIndicator inline label="Opening sign-in" /> : item.me.standing === "reconnect" ? "Reconnect" : "Connect"}
                                    </button>
                                ) : item.me.account && item.mode === "PER_MEMBER" ? (
                                    <button type="button" className="b b--s b--quiet" disabled={flow.pending} onClick={() => flow.connect(target(item))}>Use another</button>
                                ) : null}
                            </div>
                            {useHere.isError && <Problem>{connectorProblem(useHere.error, "It couldn’t be added here just now.")}</Problem>}
                        </section>
                    )}

                    {may && item.mode === "PER_MEMBER" && item.coverage && item.coverage.total > 0 && (
                        <WhoConnected pod={pod} item={item} me={me} />
                    )}

                    <Purpose pod={pod} item={item} may={may} onChanged={onChanged} />
                </div>

                {(item.me.account || may) && (
                    <footer className="ksheet__foot kpc-foot">
                        {item.me.account && (
                            <button type="button" className="b b--s b--secondary b--red" onClick={() => { drop.reset(); setRemoving("mine"); }}>Disconnect my account</button>
                        )}
                        {may && (
                            <button type="button" className="b b--s b--secondary b--red" onClick={() => { drop.reset(); setRemoving("app"); }}>Remove {app} from {pod.name}</button>
                        )}
                    </footer>
                )}
            </aside>

            {removing && (
                <ConfirmDialog
                    title={removing === "app" ? "Remove " + app + " from " + pod.name + "?" : "Disconnect your " + app + " account from " + pod.name + "?"}
                    line={removing === "app"
                        ? "Agents, apps and schedules in " + pod.name + " that use " + app + (named ? " as " + item.slug : "") + " stop working. Everyone’s accounts stay connected to the organization."
                        : mineShared
                            ? "Nothing in " + app + " runs here until someone shares an account. Your account stays connected to the organization."
                            : pod.name + " stops using your " + app + " until you connect it again. Your account stays connected to the organization."}
                    confirm={removing === "app" ? "Remove" : "Disconnect"}
                    working={removing === "app" ? "Removing…" : "Disconnecting…"}
                    danger
                    busy={drop.isPending}
                    problem={drop.isError ? connectorProblem(drop.error, "It couldn’t be done just now.") : null}
                    onConfirm={() => drop.mutate()}
                    onClose={() => setRemoving(null)}
                />
            )}
        </>,
        document.body,
    );
}

/** One choice under "Runs as": the whole card is the target. */
function Choice({ name, checked, disabled, title, children, onPick }: {
    name: string; checked: boolean; disabled: boolean; title: string; children: ReactNode; onPick: () => void;
}) {
    return (
        <label className={"kopt kopt--compact" + (disabled && !checked ? " kpc-opt--off" : "")}>
            <input type="radio" name={"runs-as-" + name} checked={checked} disabled={disabled && !checked} onChange={onPick} />
            <span className="kopt__t">{title}</span>
            <span className="kopt__d">{children}</span>
            <span className="kopt__dot" aria-hidden="true" />
        </label>
    );
}

/** Who of the people here has connected a per-member app, and asking the
 *  rest. Names, never their accounts. Everyone is listed only when the
 *  people here are exactly the people counted; otherwise the count and the
 *  people it waits on. */
function WhoConnected({ pod, item, me }: { pod: Pod; item: PodConnection; me: string }) {
    const coverage = useQuery({
        queryKey: ["pod-connectors", pod.id, "coverage", item.slug],
        queryFn: () => source.podConnectorCoverage(pod.id, item.slug),
        staleTime: 30_000,
        retry: false,
    });
    const [asked, setAsked] = useState<string[]>([]);
    const ask = useMutation({
        mutationFn: (userId: string) => source.askMembersToConnect(pod.id, item.slug, [userId]),
        onSuccess: (_said, userId) => setAsked((was) => [...was, userId]),
    });
    const people = pod.members.filter((one) => one.kind === "person" && one.userId);
    const data = coverage.data;
    const waiting = new Map((data?.waitingOn ?? []).map((one) => [one.userId, one]));
    const whole = data && !data.truncated && people.length === data.eligible;
    const rows = whole ? people.map((one) => ({ userId: one.userId ?? "", name: one.userId === me ? "You" : one.name }))
        : (data?.waitingOn ?? []).map((one) => ({ userId: one.userId, name: one.userId === me ? "You" : one.name }));

    return (
        <section className="kpc-part" aria-label="Who’s connected">
            <h3 className="kpc-part__h">Who’s connected <span className="kpc-count">{connectedCount(item.coverage)}</span></h3>
            {coverage.isPending && <p className="kpc-quiet"><LoadingIndicator inline label="Counting who has connected" /></p>}
            {coverage.isError && <Problem onRetry={() => void coverage.refetch()} retrying={coverage.isFetching}>Couldn’t read who has connected.</Problem>}
            {data && (
                <div className="kpc-people">
                    {rows.map((person) => {
                        const held = waiting.get(person.userId);
                        return (
                            <div key={person.userId} className="kpc-kv">
                                <span className="kpc-kv__l">
                                    <span className="kpc-av" style={markTint("person:" + person.name)} aria-hidden="true">{person.name.slice(0, 1)}</span>
                                    <span>{person.name}</span>
                                </span>
                                {!held ? <span className="kstatus">Connected</span>
                                    : person.userId === me ? <span className={"kstatus " + (held.standing === "REAUTH_REQUIRED" ? "kstatus--bad" : "kstatus--off")}>{held.standing === "REAUTH_REQUIRED" ? "Signed out" : "Not connected"}</span>
                                        : asked.includes(person.userId) ? <span className="kpc-said" role="status">Asked</span>
                                            : (
                                                <button type="button" className="b b--s b--secondary" disabled={ask.isPending} onClick={() => ask.mutate(person.userId)}>
                                                    {held.standing === "REAUTH_REQUIRED" ? "Ask to reconnect" : "Ask"}
                                                </button>
                                            )}
                            </div>
                        );
                    })}
                    {!whole && data.connected > 0 && <p className="kpc-quiet">{data.connected === 1 ? "1 other person has" : data.connected + " others have"} connected theirs.</p>}
                    {ask.isError && <Problem>{connectorProblem(ask.error, "They couldn’t be asked just now.")}</Problem>}
                </div>
            )}
        </section>
    );
}

/** What it's for, in an editor's words: shown when someone is asked to
 *  connect. */
function Purpose({ pod, item, may, onChanged }: { pod: Pod; item: PodConnection; may: boolean; onChanged: () => void }) {
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState(item.purpose);
    const save = useMutation({
        mutationFn: () => source.changePodConnector(pod.id, item.slug, { purpose: draft }),
        onSuccess: () => { setEditing(false); onChanged(); },
    });
    if (!may && !item.purpose) return null;
    return (
        <section className="kpc-part" aria-label="What it’s for">
            <h3 className="kpc-part__h">What it’s for</h3>
            {editing ? (
                <form className="kpc-purpose" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
                    <textarea className="kinput" rows={3} maxLength={500} value={draft} autoFocus aria-label="What it’s for"
                        placeholder={"What " + pod.name + " uses " + item.title + " for"} onChange={(event) => setDraft(event.target.value)} />
                    <span className="kpc-purpose__acts">
                        <button type="button" className="b b--s b--quiet" disabled={save.isPending} onClick={() => { setEditing(false); setDraft(item.purpose); }}>Cancel</button>
                        <button type="submit" className="b b--s b--primary" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save"}</button>
                    </span>
                    {save.isError && <Problem>{connectorProblem(save.error, "It couldn’t be saved just now.")}</Problem>}
                </form>
            ) : (
                <p className="kpc-line">
                    {item.purpose ? (/[.!?]$/.test(item.purpose) ? item.purpose : item.purpose + ".") : "Nothing written yet."}{" "}
                    <span className="kpc-note">Shown when someone is asked to connect.</span>{" "}
                    {may && <button type="button" className="b b--s b--quiet kpc-inline" onClick={() => { setDraft(item.purpose); setEditing(true); }}>{item.purpose ? "Edit" : "Write it"}</button>}
                </p>
            )}
        </section>
    );
}

/* ── mine, for everyone ─────────────────────────────────────────────── */

/** Running the app for everyone as my account: one confirmation, saying
 *  what it means for the others and for me. The account owner's own act,
 *  done in their own name. */
function ShareMine({ pod, item, me, onClose, onDone }: { pod: Pod; item: PodConnection; me: string; onClose: () => void; onDone: () => void }) {
    const app = item.title;
    const label = item.me.account?.label || item.me.existing?.label || "";
    const others = pod.members.filter((one) => one.kind === "person" && one.userId && one.userId !== me).map((one) => one.name);
    const share = useMutation({
        mutationFn: async () => {
            let podAccountId = item.me.account?.podAccountId ?? "";
            /* An account of mine the organization has, not in this Lemma yet:
               put in first, as mine alone, then shared. */
            if (!podAccountId && item.me.existing) podAccountId = (await source.addPodAccount(pod.id, item.me.existing.accountId)).id;
            if (!podAccountId) throw new Error("Connect your " + app + " account here first.");
            /* One request, whatever the connector runs as now: the server
               checks it all, then widens the account and switches the
               connector to it together, or does neither. */
            await source.shareAccountAs(pod.id, podAccountId, item.slug);
        },
        onSuccess: onDone,
    });
    const close = () => { if (!share.isPending) onClose(); };
    return (
        <Modal
            title={"Run " + app + " for everyone as you?"}
            narrow
            onClose={close}
            footer={<>
                <button type="button" className="b b--l b--secondary" disabled={share.isPending} onClick={close}>Cancel</button>
                <button type="button" className="b b--l b--primary" disabled={share.isPending} onClick={() => share.mutate()}>
                    {share.isPending ? "Saving…" : "Run it as me"}
                </button>
            </>}
        >
            <div className="kpc-share">
                <p className="kd-line">Every {app} call in {pod.name} will use {label ? <span className="kpc-fig">{label}</span> : "your account"}.</p>
                <ul>
                    {others.length > 0 && <li>{andList(others)} {others.length === 1 ? "stops" : "stop"} needing their own {app} to use {pod.name}.</li>}
                    <li>Agents and schedules in {pod.name} act as you, even when you’re away.</li>
                    <li>Only {pod.name}. Your {app} stays your own in every other Lemma.</li>
                    <li>You can switch back to “each person’s own” any time. Nobody else can widen it.</li>
                </ul>
                {share.isError && <p className="kd-problem" role="alert">{connectorProblem(share.error, "It couldn’t be shared just now.")}</p>}
            </div>
        </Modal>
    );
}

function andList(names: string[]): string {
    return names.length < 2 ? names.join("") : names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
}

/* ── adding an app ──────────────────────────────────────────────────── */

/** Pick an app and you're signing in: it's added as each person's own, under
 *  a name made for it, and nothing is asked. An account of mine the
 *  organization already has is put in at once, with no sign-in. GitHub, where
 *  the organization's App is installed, offers the bot first. */
function AddApp({ pod, connections, catalog, installs, titleOf, flow, onClose, onAdded }: {
    pod: Pod;
    connections: Connections;
    catalog: Connector[];
    installs: Install[];
    titleOf: (connectorId: string) => string;
    flow: Flow;
    onClose: () => void;
    onAdded: (slug: string) => void;
}) {
    const [query, setQuery] = useState("");
    const [botFor, setBotFor] = useState<AddChoice | null>(null);
    const accounts = useQuery({ queryKey: ["accounts", pod.orgId], queryFn: () => source.listAccounts(pod.orgId), staleTime: 30_000 });
    const items = connections.items;
    const taken = items.map((one) => one.slug);
    const choices = addChoices(installs, catalog, titleOf);
    const term = query.trim().toLowerCase();
    const shown = choices.filter((one) => !term || one.label.toLowerCase().includes(term) || one.connectorId.includes(term));
    const mineOn = (choice: AddChoice) => (accounts.data ?? []).find((one) => one.usable && (choice.installId ? one.authConfigId === choice.installId : one.connectorId === choice.connectorId)) ?? null;
    const here = (choice: AddChoice) => items.some((one) => (choice.installId && one.authConfigId ? one.authConfigId === choice.installId : one.connectorId === choice.connectorId) && one.mode !== "APP");
    const botOffered = (choice: AddChoice) => hasBotMode(choice.connectorId) && connections.githubBot !== null && !items.some((one) => one.mode === "APP");
    const entryOf = (id: string) => catalog.find((one) => one.id === id) ?? null;

    const add = useMutation({
        mutationFn: async ({ choice, bot }: { choice: AddChoice; bot: boolean }) => {
            if (bot) {
                const made = await source.addPodConnector(pod.id, {
                    slug: botSlug(taken), authConfigId: "", connectorId: choice.connectorId, mode: "APP", sharedPodAccountId: "", required: false, purpose: "",
                });
                return { made, connected: true };
            }
            const made = await source.addPodConnector(pod.id, {
                slug: freshSlug(choice.connectorId, taken), authConfigId: choice.installId, connectorId: choice.connectorId,
                mode: "PER_MEMBER", sharedPodAccountId: "", required: false, purpose: "",
            });
            const mine = mineOn(choice);
            if (mine) await source.addPodAccount(pod.id, mine.id);
            return { made, connected: Boolean(mine) };
        },
        onSuccess: ({ made, connected }, { choice }) => {
            onAdded(made.slug);
            if (!connected) {
                flow.connect({ slug: made.slug, connectorId: made.connectorId || choice.connectorId, authConfigId: made.authConfigId || choice.installId, installationName: made.installationName });
            }
        },
    });

    const tile = (choice: AddChoice) => {
        const inHere = here(choice) && !botOffered(choice);
        const mine = mineOn(choice);
        const sub = inHere ? "In " + pod.name + " already" : choice.from === "install" ? (mine ? "Connected for you" : "Not connected") : "";
        return (
            <button key={choice.key} type="button" className="kpc-tile" disabled={inHere || add.isPending}
                onClick={() => { add.reset(); if (botOffered(choice)) setBotFor(choice); else add.mutate({ choice, bot: false }); }}>
                <ServiceMark entry={{ title: titleOf(choice.connectorId), icon: entryOf(choice.connectorId)?.icon ?? "" }} />
                <span>{choice.label}{sub && <small>{sub}</small>}</span>
            </button>
        );
    };

    if (botFor) {
        const bot = connections.githubBot;
        return (
            <Modal title={"Add " + titleOf(botFor.connectorId) + " to " + pod.name} narrow onClose={() => { if (!add.isPending) onClose(); }}
                footer={<>
                    <button type="button" className="b b--l b--secondary" disabled={add.isPending} onClick={() => setBotFor(null)}>Back</button>
                    <button type="button" className="b b--l b--primary" disabled={add.isPending} onClick={() => add.mutate({ choice: botFor, bot: true })}>
                        {add.isPending && add.variables?.bot ? "Adding…" : "Use the bot"}
                    </button>
                </>}>
                <div className="kpc-share">
                    <p className="kd-line">
                        {pod.name} can read and post as {botLogins(bot)}’s GitHub App. Nobody has to connect or share an account. {botReach(bot)}
                    </p>
                    <p className="kd-line">
                        Or <button type="button" className="b b--s b--quiet kpc-inline" disabled={add.isPending} onClick={() => add.mutate({ choice: botFor, bot: false })}>sign in with your own GitHub</button> instead, as each person’s own.
                    </p>
                    {add.isError && <p className="kd-problem" role="alert">{connectorProblem(add.error, "It couldn’t be added just now.")}</p>}
                </div>
            </Modal>
        );
    }

    /* What can be added first; the apps already here last, said as such. */
    const order = (list: AddChoice[]) => [...list.filter((one) => !here(one) || botOffered(one)), ...list.filter((one) => here(one) && !botOffered(one))];
    const installed = order(shown.filter((one) => one.from === "install"));
    const more = order(shown.filter((one) => one.from === "catalog"));
    return (
        <Modal title={"Add an app to " + pod.name} subtitle={"You’ll sign in with your own account. Others connect theirs the first time " + pod.name + " needs it."}
            onClose={() => { if (!add.isPending) onClose(); }}>
            <div className="kpc-add">
                <label className="ksearch kpc-find">
                    <SearchIcon size={15} />
                    <input value={query} autoFocus placeholder="Search apps" aria-label="Search apps" onChange={(event) => setQuery(event.target.value)} />
                </label>
                {add.isError && <Problem>{connectorProblem(add.error, "It couldn’t be added just now.")}</Problem>}
                {installed.length > 0 && (
                    <section aria-label="Already used in your organization">
                        <h3 className="kpc-group">Already used in your organization</h3>
                        <div className="kpc-tiles">{installed.map(tile)}</div>
                    </section>
                )}
                {more.length > 0 && (
                    <section aria-label="More apps">
                        <h3 className="kpc-group">More apps</h3>
                        <div className="kpc-tiles">{more.map(tile)}</div>
                    </section>
                )}
                {shown.length === 0 && (
                    <p className="kpc-quiet">{term ? "Nothing matches “" + query + "”." : "There’s nothing to add yet."}</p>
                )}
                <p className="kpc-quiet">
                    Your own server or database? <a className="kpc-link" href="/connectors">Add an MCP server, REST API or PostgreSQL</a>
                </p>
            </div>
        </Modal>
    );
}
