import { fields } from "@/connect/schema";
import { surfaceStatus, surfacesForAgent } from "@/data/surface-settings";
import { SurfaceCredentials } from "./surface-credentials";
import { SurfaceGuide } from "./surface-setup";
import { SurfaceManage } from "./surface-manage";
import { LoadingIndicator } from "@/ui/loading";
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import { accountName, bestAccount, blurbOf, isCredentialConflict } from "@/data";
import type { AccountConnect, Connectable, ConnectorAccount, GuidedSetup, Pod, Surface } from "@/data";
import { BackIcon, CheckIcon, CopyIcon, ExternalIcon, RefreshIcon } from "@/ui/icons";
import { KnownSender } from "@/session/mobile-verification";
import { OwnBot } from "./own-bot";
import { ChannelIcon, channelKey, channelName } from "./channels";
import { Modal } from "./modal";
import { Mark } from "./mark";
import { copyText } from "@/desktop/clipboard";

/** Giving a teammate a way to be reached.
 *
 *  The old flow was four identical grey icons and a modal whose only real job
 *  was to open a different app in a new tab. It never said what connecting
 *  would cost, because it never asked: `podSurfaces.available()` answers that
 *  per platform and nothing read it.
 *
 *  So this sorts by effort and says the effort out loud. One click where a
 *  Lemma-run identity can answer, about a minute where a bot gets made for
 *  you, and a trip to the settings app only where an account genuinely has to
 *  be authorised. Three different amounts of work should not look identical.
 *
 *  And it ends on the **address**. "Connected" is not what anyone wanted —
 *  they wanted somewhere to write to. So the finished state is the handle,
 *  large, copyable, next to a link that opens the conversation. */

/** Platforms where a teammate can be given an identity of its own by
 *  registering an app. Slack today; kept in one place so the reason is stated
 *  once rather than spelled as a condition in three. */
const OWN_BOT = new Set(["SLACK"]);

/** Where a person goes to actually say something. */
function sayHi(surface: Surface): string | null {
    const key = channelKey(surface.platform);
    const handle = surface.handle.trim();
    if (!handle) return null;
    if (key === "TELEGRAM") return "https://t.me/" + handle.replace(/^@/, "");
    if (key === "WHATSAPP") return "https://wa.me/" + handle.replace(/[^\d]/g, "");
    if (key === "EMAIL") return "mailto:" + (surface.email || handle).trim().replace(/^(mailto:)+/i, "").replace(/\\@/g, "@");
    return null;
}

function Handle({ surface }: { surface: Surface }) {
    const [copied, setCopied] = useState(false);
    const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
    useEffect(() => () => clearTimeout(timer.current), []);
    return (
        <button
            className="reachrow__handle"
            title="Copy"
            onClick={() => {
                clearTimeout(timer.current);
                copyText(surface.email ?? surface.handle)
                    .then(() => {
                        setCopied(true);
                        timer.current = setTimeout(() => setCopied(false), 1600);
                    })
                    .catch(() => undefined);
            }}
        >
            <span>{surface.handle}</span>
            {copied ? <CheckIcon size={14} /> : <CopyIcon size={14} />}
            <span className="sr-only" role="status">{copied ? "Copied" : ""}</span>
        </button>
    );
}

/** What this will cost, in words, before anything is clicked. */
const COST: Record<string, string> = {
    instant: "Ready now",
    guided: "About a minute",
    account: "Needs your account",
    unavailable: "Not available",
};

function ConnectedRow({ surface, pod, onDrop, onManage }: { surface: Surface; pod: Pod; onDrop: (name: string) => void; onManage: () => void }) {
    const [confirming, setConfirming] = useState(false);
    const open = sayHi(surface);
    return (
        <li className="reachrow reachrow--on">
            <span className="reachrow__mark"><ChannelIcon platform={surface.platform} size={20} /></span>
            <div className="reachrow__body">
                <b>{channelName(surface.platform)}</b>
                {surface.handle && <Handle surface={surface} />}
                <span className="reachrow__note">{surfaceStatus(surface.status, surface.active)}</span>
                {!surface.mine && (
                    /* A surface on this pod that answers as a different agent.
                       Drawn like the pod's own, it says "Marketing is on
                       Telegram" about a bot that is not Marketing. */
                    <span className="reachrow__note">answers as {surface.agentName || "another teammate"}</span>
                )}
            </div>
            <div className="reachrow__acts">
                <button className="btn" onClick={onManage}>Manage</button>
                {open && surface.active && (
                    <a className="btn" href={open} target={open.startsWith("mailto:") ? undefined : "_blank"} rel="noreferrer">
                        Say hi <ExternalIcon size={13} />
                    </a>
                )}
                {confirming ? (
                        <>
                            <button className="btn reachrow__drop" onClick={() => onDrop(surface.name)}>
                                Disconnect
                            </button>
                            <button className="linkish" onClick={() => setConfirming(false)}>Keep</button>
                        </>
                    ) : (
                        <button
                            className="linkish reachrow__quiet"
                            onClick={() => setConfirming(true)}
                            aria-label={"Disconnect " + channelName(surface.platform) + " from " + pod.name}
                        >
                            Disconnect
                        </button>
                    )}
            </div>
            {/* The address above is only half of a working channel. On
                WhatsApp the sender's number is the only identity a message
                carries, so a member with no number on their profile writes to
                the teammate they were just given and is answered by a sign-up
                link. Said here, under the address, rather than discovered
                there. */}
            {surface.mine && channelKey(surface.platform) === "WHATSAPP" && <KnownSender />}
        </li>
    );
}

/** The guided path: Lemma's manager bot makes you a bot of your own.
 *
 *  Three legs — start here, finish in Telegram, come back — so the waiting is
 *  real and gets its own state rather than a spinner that lies. */
function Guided({ pod, onDone }: { pod: Pod; onDone: () => void }) {
    const [setup, setSetup] = useState<GuidedSetup | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [starting, setStarting] = useState(false);

    const ready = setup?.status === "COMPLETE" || setup?.status === "READY";
    const failed = setup?.status === "FAILED" || Boolean(setup?.expiresAt && Date.parse(setup.expiresAt) < Date.now());

    useEffect(() => {
        if (!setup || ready || failed) return;
        let stop = false;
        const tick = window.setInterval(() => {
            source
                .checkGuided(pod.id, setup.setupId)
                .then((next) => {
                    if (stop) return;
                    setSetup(next);
                    if (next.status === "READY" || next.status === "COMPLETE") onDone();
                })
                .catch(() => setError("Could not check setup. We will try again."));
        }, 2500);
        return () => {
            stop = true;
            window.clearInterval(tick);
        };
    }, [setup, ready, failed, pod.id, onDone]);

    if (!setup) {
        return (
            <button
                className="btn"
                disabled={starting}
                onClick={() => {
                    setStarting(true);
                    setError(null);
                    source
                        .startGuided(pod.id, "TELEGRAM")
                        .then(next => { setSetup(next); if (next.status === "COMPLETE" || next.status === "READY") onDone(); })
                        .catch((problem) =>
                            setError(problem instanceof Error ? problem.message : "That could not be started."),
                        )
                        .finally(() => setStarting(false));
                }}
            >
                {starting ? "Starting…" : "Make a bot of its own"}
                {error && <span role="alert">{error}</span>}
            </button>
        );
    }

    if (failed) return <div role="alert"><p>{setup.error || "This setup expired or failed."}</p><button className="btn" onClick={() => { setSetup(null); setError(null); }}>Start again</button></div>;

    if (ready) {
        return (
            <p className="guided guided--done">
                <CheckIcon size={15} /> {setup.botUsername} is live.
            </p>
        );
    }

    return (
        <div className="guided">
            <a className="btn btn--primary" href={setup.launchUrl} target="_blank" rel="noreferrer">
                Open Telegram <ExternalIcon size={14} />
            </a>
            <p>
                {setup.managerBot} will ask you for a name, then make the bot. This page notices when it is done —
                you can leave it open.
            </p>
            {error && <p role="alert">{error}</p>}
            <span className="guided__wait">
                <RefreshIcon size={13} /> Waiting for Telegram…
            </span>
        </div>
    );
}

/** Why this row costs what it costs.
 *
 *  The sentence that matters most is the one about a shared identity somebody
 *  else already took: it is the difference between "Slack needs a sign-in
 *  because Slack always does" and "WhatsApp needs one because your org spent
 *  its free number on another teammate" — and only one of those is worth
 *  knowing. It is reported whenever the platform HAS a shared identity and
 *  this org cannot have it, not only when nothing else is left. */
function why(entry: Connectable): string {
    if (entry.system && !entry.systemFree) {
        return (
            "Lemma's shared " +
            (entry.platform === "WHATSAPP" ? "number" : "identity") +
            " is already taken by another teammate in this organization."
        );
    }
    if (entry.effort === "instant" && entry.platform === "RESEND" && entry.emailDomain) {
        return "An address of its own on " + entry.emailDomain + ".";
    }
    return blurbOf(entry);
}

/** Authorising an account, without leaving for a settings page.
 *
 *  Not a button reading "Sign in" that opens the platform's channel settings
 *  in a new tab — which is not a sign-in, and is not where the work happens
 *  either. The real shape is the same three legs as the bot setup: ask the
 *  backend for an authorization URL, send the person to the provider's own
 *  consent page, and watch for the account that appears. Only the consent page
 *  is somewhere else, and that part genuinely has to be.
 *
 *  The account is recognised by its authorization request. There is
 *  no callback to this app — the provider redirects to the backend — so a
 *  new row on the connector is the signal. */
function Account({
    entry,
    pod,
    onDone,
}: {
    entry: Connectable;
    pod: Pod;
    onDone: () => void;
}) {
    const [link, setLink] = useState<AccountConnect | null>(null);
    const [stage, setStage] = useState<"idle" | "starting" | "waiting" | "binding">("idle");
    const [error, setError] = useState<string | null>(null);
    const name = entry.title || channelName(entry.platform);
    const [pendingAccount, setPendingAccount] = useState<string | null>(null);

    useEffect(() => {
        if (!link || stage !== "waiting") return;
        let stop = false;
        const tick = window.setInterval(() => {
            source
                .findAccount(pod.orgId, entry.connectorId, link.before)
                .then(async (accountId) => {
                    if (stop || !accountId) return;
                    stop = true;
                    window.clearInterval(tick);
                    setStage("binding");
                    setPendingAccount(accountId);
                    try {
                        await source.connectAccount(pod.id, entry.platform, accountId);
                        onDone();
                    } catch (problem) {
                        setStage("idle");
                        setLink(null);
                        setError(problem instanceof Error ? problem.message : "That account could not be used.");
                    }
                })
                .catch((problem) => {
                    if (stop) return;
                    setError(problem instanceof Error ? problem.message : "That account could not be used.");
                });
        }, 2500);
        return () => {
            stop = true;
            window.clearInterval(tick);
        };
    }, [link, stage, pod.orgId, pod.id, entry.connectorId, entry.platform, onDone]);

    if (pendingAccount && stage !== "binding") return <div className="guided">
        <p role="alert">{error}</p>
        <button className="btn" onClick={async () => {
            setStage("binding"); setError(null);
            try { await source.connectAccount(pod.id, entry.platform, pendingAccount); onDone(); }
            catch (problem) { setStage("idle"); setError(problem instanceof Error ? problem.message : "Could not connect."); }
        }}>Retry connection</button>
    </div>;

    if (stage === "binding") {
        return (
            <div className="guided">
                <span className="guided__wait"><RefreshIcon size={13} /> Setting it up…</span>
            </div>
        );
    }

    if (link && link.authorizeUrl) {
        return (
            <div className="guided">
                <a className="btn btn--primary" href={link.authorizeUrl} target="_blank" rel="noreferrer">
                    Authorise {name} <ExternalIcon size={14} />
                </a>
                <p>{name} asks whether Lemma may act for you. This page notices when you are done.</p>
                <span className="guided__wait"><RefreshIcon size={13} /> Waiting for {name}…</span>
            </div>
        );
    }

    return (
        <>
            <button
                className="btn"
                disabled={stage === "starting"}
                onClick={() => {
                    setStage("starting");
                    setError(null);
                    source
                        .startAccount(pod.orgId, entry.connectorId)
                        .then((started) => {
                            setLink(started);
                            /* No URL means this deployment cannot start the
                               authorisation itself. Saying so beats a button
                               that looks live and does nothing. */
                            setStage(started.authorizeUrl ? "waiting" : "idle");
                            if (!started.authorizeUrl) setError("This one has to be connected in Lemma's settings.");
                        })
                        .catch((problem) => {
                            setStage("idle");
                            setError(problem instanceof Error ? problem.message : "That could not be started.");
                        });
                }}
            >
                {/* Just "Connect": the row's own heading already says which
                    platform, and "Connect Microsoft Teams" was wide enough to
                    squeeze the description into a three-line column. */}
                {stage === "starting" ? <LoadingIndicator inline label="Loading" /> : "Connect"}
            </button>
            {error && <span className="reachrow__error">{error}</span>}
        </>
    );
}

function ConnectRow({
    entry,
    busy,
    onConnect,
    accounts,
    onUseAccount,
    onFocus,
    elsewhere,
}: {
    entry: Connectable;
    busy: boolean;
    onConnect: (platform: string) => void;
    accounts: ConnectorAccount[];
    onUseAccount: (entry: Connectable, accountId: string) => void;
    onFocus: (entry: Connectable) => void;
    /** Another teammate in this organization already answers here. */
    elsewhere: boolean;
}) {
    const name = entry.title || channelName(entry.platform);
    /* An account the organization already authorised is not something to make
       somebody authorise again. This is also the case that was quietly broken:
       re-authorising a connector the org already has can refresh the existing
       row instead of inserting a new one, so a flow that waits for a NEW
       account waits forever. Finding the existing one first removes both the
       wait and the reason for it. */
    /* An account this organization already has — but only worth offering when
       nothing else is already answering on this platform. A connected account
       belongs to one surface org-wide, so if another teammate is on Slack the
       odds are this account is the one they are on, and "Ready now" would be a
       button whose only outcome is a 409. The backend is the authority and
       still gets the last word; this just stops the UI promising ahead of it. */
    const held = entry.effort === "account" ? bestAccount(accounts, entry.connectorId) : null;
    const ready = elsewhere ? null : held;
    const guiding = entry.effort === "guided";
    const note = why(entry);

    return (
        <li className="reachrow" data-effort={entry.effort}>
            <span className="reachrow__mark"><ChannelIcon platform={entry.platform} size={20} /></span>
            <div className="reachrow__body">
                <b>{name}</b>
                {note && <span className="reachrow__note">{note}</span>}
                {ready && !guiding && (
                    /* The organization's, not this teammate's — the account is
                       org-scoped, and "already connected here" implied this
                       pod had it. */
                    <span className="reachrow__note">
                        Using {accountName(ready, name)}, already connected to this organization.
                    </span>
                )}
                {elsewhere && held && !guiding && (
                    <span className="reachrow__note">
                        Another teammate already answers on {name}, so this one needs an identity of its own.
                    </span>
                )}
                {/* A choice, not only a fallback: a bot carrying this
                    teammate's own name is a different — often better —
                    outcome than sharing one. Offered wherever it is possible,
                    which is the guided platforms and Slack; on Slack it is
                    also the ONLY way for a second teammate, since a connected
                    account is claimable once per organization. */}
                {/* Not when the row's own button already says exactly this. */}
                {(entry.guided || OWN_BOT.has(entry.platform) || fields(entry.credentialSchema).length > 0) && !guiding && !(elsewhere && held) && (
                    <button className="linkish reachrow__alt" onClick={() => onFocus(entry)}>
                        Use your own account or bot
                    </button>
                )}
            </div>
            <div className="reachrow__acts">
                <span className="reachrow__cost" data-effort={ready ? "instant" : entry.effort}>
                    {ready ? COST.instant : elsewhere && held ? "Already in use" : COST[entry.effort]}
                </span>
                {guiding ? (
                    <button className="btn" onClick={() => onFocus(entry)}>Set up</button>
                ) : entry.effort === "instant" ? (
                    <button className="btn btn--primary" disabled={busy} onClick={() => onConnect(entry.platform)}>
                        {busy ? "Connecting…" : "Connect"}
                    </button>
                ) : ready ? (
                    <button className="btn btn--primary" disabled={busy} onClick={() => onUseAccount(entry, ready.id)}>
                        {busy ? "Connecting…" : "Connect"}
                    </button>
                ) : entry.effort === "account" || entry.effort === "unavailable" ? (
                    <button
                        className={"btn" + (elsewhere && held ? " btn--primary" : "")}
                        onClick={() => onFocus(entry)}
                    >
                        {elsewhere && held ? "Give it its own" : "Set up"}
                    </button>
                ) : null}
            </div>
        </li>
    );
}

/** One channel, on its own, with one thing to do.
 *
 *  The list answers "where could this teammate be?"; it is the wrong shape for
 *  "and how do I set this one up", which is a few sentences and a single
 *  action. So picking a channel that needs real work replaces the list rather
 *  than nesting a second dialog inside it — the focused treatment from the
 *  original connect modal, which read better than a row ever will, without
 *  that modal's habit of sending you somewhere else to do the work. */
function Focused({
    entry,
    pod,
    onBack,
    onDone,
    taken,
}: {
    entry: Connectable;
    pod: Pod;
    onBack: () => void;
    onDone: () => void;
    /** The account was refused because another teammate holds it. */
    taken?: string;
}) {
    const name = entry.title || channelName(entry.platform);
    const canOwn = OWN_BOT.has(entry.platform);
    const [custom, setCustom] = useState(false);
    const hasCredentials = fields(entry.credentialSchema).length > 0;
    const [ownBot, setOwnBot] = useState(canOwn && (Boolean(taken) || entry.effort === "unavailable"));

    return (
        <div className="focused">
            <button className="focused__back" onClick={onBack}>
                <BackIcon size={15} /> All channels
            </button>

            <span className="focused__logo"><ChannelIcon platform={entry.platform} size={38} /></span>

            <SurfaceGuide podId={pod.id} platform={entry.platform} />
            {((custom || !entry.guided) && hasCredentials) ? (
                <>
                    <h3>Connect your {name} account</h3>
                    <SurfaceCredentials pod={pod} entry={entry} onDone={onDone} />
                    {entry.guided && <button className="linkish" onClick={() => setCustom(false)}>Create a new bot instead</button>}
                </>
            ) : ownBot && canOwn ? (
                <>
                    <h3>Give {pod.name} a Slack bot of its own</h3>
                    <p>
                        {taken
                            ? "That Slack account already answers as " + taken + ". One account belongs to one teammate, and one Slack app is one bot user — so this one needs an app of its own."
                            : "One Slack app is one bot user, so a teammate that answers under its own name needs an app under its own name. Four steps, and the manifest does most of them."}
                    </p>
                    <OwnBot entry={entry} pod={pod} onDone={onDone} />
                </>
            ) : entry.guided ? (
                <>
                    <h3>Give {pod.name} a bot of its own</h3>
                    <p>
                        Lemma&rsquo;s setup bot makes it for you and names it after this teammate. You will not need
                        to touch BotFather.
                    </p>
                    <Guided pod={pod} onDone={onDone} />
                    {hasCredentials && <button className="linkish" onClick={() => setCustom(true)}>Use an existing bot</button>}
                </>
            ) : (
                <>
                    <h3>Connect {name} to {pod.name}</h3>
                    <p>
                        {name} will ask whether Lemma may act for you. Nothing is sent anywhere until you say so —
                        this only gives {pod.name} somewhere to answer.
                    </p>
                    {entry.account ? <Account entry={entry} pod={pod} onDone={onDone} /> : <p>This platform is not configured on this deployment. Follow the setup instructions or ask your administrator.</p>}
                    {canOwn && (
                        <button className="linkish focused__alt" onClick={() => setOwnBot(true)}>
                            Or give {pod.name} a Slack bot of its own
                        </button>
                    )}
                </>
            )}
        </div>
    );
}

export function ReachSheet({ pod, onClose }: { pod: Pod; onClose: () => void }) {
    const queryClient = useQueryClient();
    const [managing, setManaging] = useState<Surface | null>(null);
    const [completionError, setCompletionError] = useState<string | null>(null);
    const [focus, setFocus] = useState<Connectable | null>(null);
    const surfaces = useQuery({
        queryKey: ["surfaces", 2, pod.id],
        queryFn: () => source.listSurfaces(pod.id),
        staleTime: 60_000,
    });
    const catalog = useQuery({
        queryKey: ["connectable", pod.id],
        queryFn: () => source.listConnectable(pod.id),
        staleTime: 5 * 60_000,
    });
    const accounts = useQuery({
        queryKey: ["accounts", pod.orgId],
        queryFn: () => source.listAccounts(pod.orgId),
        staleTime: 60_000,
    });
    /* One call for every pod's surfaces, which is the only place this app can
       learn that a sibling teammate is already on a platform. */
    const mine = useQuery({
        queryKey: ["my-surfaces"],
        queryFn: () => source.listMySurfaces(),
        staleTime: 60_000,
    });

    const refresh = () => {
        void queryClient.invalidateQueries({ queryKey: ["surfaces", 2, pod.id] });
        void queryClient.invalidateQueries({ queryKey: ["connectable", pod.id] });
        void queryClient.invalidateQueries({ queryKey: ["accounts", pod.orgId] });
        void queryClient.invalidateQueries({ queryKey: ["my-surfaces"] });
        void queryClient.invalidateQueries({ queryKey: ["surface-detail", pod.id] });
        void queryClient.invalidateQueries({ queryKey: ["surface-setup", pod.id] });
        void queryClient.invalidateQueries({ queryKey: ["surface-channels", pod.id] });
    };

    const finish = async (platform: string) => {
        refresh();
        setFocus(null);
        setTaken(undefined);
        try {
            const updated = await source.listSurfaces(pod.id);
            const made = updated.find(surface => surface.platform === platform && surface.mine);
            if (made) setManaging(made);
            else setCompletionError("Connection saved. Refresh channels to finish setup.");
        } catch { setCompletionError("Connection saved, but setup could not be loaded. Refresh channels to continue."); }
    };

    const connect = useMutation({
        mutationFn: (platform: string) => source.connectSystem(pod.id, platform),
        onSuccess: (surface) => { refresh(); setManaging(surface); },
    });
    const [taken, setTaken] = useState<string | undefined>(undefined);
    const useAccount = useMutation({
        mutationFn: ({ entry, accountId }: { entry: Connectable; accountId: string }) =>
            source.connectAccount(pod.id, entry.platform, accountId),
        onSuccess: (surface) => { refresh(); setManaging(surface); },
        onError: (problem, variables) => {
            /* Not a failure to report and stop at: the account is spoken for,
               and the answer is an identity of this teammate's own. */
            if (!isCredentialConflict(problem)) return;
            setTaken("another teammate");
            setFocus(variables.entry);
        },
    });
    const drop = useMutation({
        mutationFn: (name: string) => source.disconnect(pod.id, name),
        onSuccess: refresh,
    });

    const live = surfaces.data ?? [];
    const on = surfacesForAgent(live);

    /* A platform already answering for this pod is not something to offer
       again. Keyed on the channel rather than the platform string so Resend
       and "EMAIL" are one thing, as they are everywhere else. */
    const already = useMemo(
        () => new Set(on.map((surface) => channelKey(surface.platform))),
        [on],
    );
    const offer = (surfaces.isSuccess ? catalog.data ?? [] : []).filter((entry) => !already.has(channelKey(entry.platform)));

    const heldElsewhere = useMemo(
        () =>
            new Set(
                (mine.data ?? [])
                    .filter((surface) => surface.podId !== pod.id)
                    .map((surface) => channelKey(surface.platform))
                    .concat(live.filter(surface => !surface.mine).map(surface => channelKey(surface.platform))),
            ),
        [mine.data, pod.id],
    );

    const failed = connect.error ?? drop.error ?? (isCredentialConflict(useAccount.error) ? null : useAccount.error);

    return (
        <Modal
            title={"Where can you reach " + pod.name + "?"}
            subtitle="Pick a channel and your teammate answers there, under its own name."
            onClose={onClose}
        >
            {managing ? <SurfaceManage pod={pod} surface={managing} onBack={() => setManaging(null)} onSaved={() => { refresh(); setManaging(null); }} /> : focus ? (
                <Focused
                    entry={focus}
                    pod={pod}
                    taken={taken}
                    onBack={() => {
                        setFocus(null);
                        setTaken(undefined);
                    }}
                    onDone={() => { void finish(focus.platform); }}
                />
            ) : (
            <div className="reachsheet">
                <div className="reachsheet__who">
                    <Mark seed={pod.id} name={pod.name} icon={pod.iconUrl} size={40} />
                    <p>
                        {on.length === 0
                            ? pod.name + " can only be reached here, in Lemma."
                            : pod.name + " has " + on.length + (on.length === 1 ? " channel." : " channels.")}
                    </p>
                </div>

                {on.length > 0 && (
                    <ul className="reachrows">
                        {on.map((surface) => (
                            <ConnectedRow key={surface.id} surface={surface} pod={pod} onDrop={(name) => drop.mutate(name)} onManage={() => setManaging(surface)} />
                        ))}
                    </ul>
                )}

                {(catalog.isPending || surfaces.isPending) && <p className="empty-row">Reading the channels…</p>}

                {catalog.isError && (
                    <button className="btn" onClick={() => void catalog.refetch()}>
                        <RefreshIcon size={15} /> Retry
                    </button>
                )}

                {offer.length > 0 && (
                    <>
                        <h3 className="reachsheet__more">{on.length > 0 ? "Somewhere else" : "Pick a channel"}</h3>
                        <ul className="reachrows">
                            {offer.map((entry) => (
                                <ConnectRow
                                    key={entry.platform}
                                    entry={entry}
                                    busy={
                                        (connect.isPending && connect.variables === entry.platform) ||
                                        (useAccount.isPending && useAccount.variables?.entry.platform === entry.platform)
                                    }
                                    onConnect={(platform) => connect.mutate(platform)}
                                    onUseAccount={(chosen, accountId) => useAccount.mutate({ entry: chosen, accountId })}
                                    onFocus={setFocus}
                                    elsewhere={heldElsewhere.has(channelKey(entry.platform))}
                                    accounts={accounts.data ?? []}
                                />
                            ))}
                        </ul>
                    </>
                )}

                {completionError && <p role="alert">{completionError} <button className="btn" onClick={() => { refresh(); setCompletionError(null); }}>Refresh channels</button></p>}
                {surfaces.isError && <p role="alert">Could not read existing channels. <button className="btn" onClick={() => void surfaces.refetch()}>Retry</button></p>}
                {failed && (
                    <p className="approval__error">
                        {failed instanceof Error ? failed.message : "Couldn’t update this channel."}
                    </p>
                )}
            </div>
            )}
        </Modal>
    );
}
