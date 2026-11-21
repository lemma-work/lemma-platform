import { fields } from "@/connect/schema";
import { surfaceStatus, surfacesForAgent } from "@/data/surface-settings";
import { SurfaceCredentials } from "./surface-credentials";
import { SetUpOnThisMac } from "@/desktop/set-up-on-this-mac";
import { credentialFormForChannel, oauthFormForConnector } from "@/desktop/this-mac";
import { useThisMacAvailability } from "@/desktop/this-mac-settings";
import { useThisComputer } from "@/desktop/this-computer";
import { openSettings } from "@/desktop/open-settings";
import { SurfaceManage } from "./surface-manage";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import QRCode from "react-qr-code";
import { source } from "@/data";
import { blurbOf, connectionsPath, connectorsFor, isCredentialConflict, needsSharing, statusNote, unavailableNote } from "@/data";
import type { AccountConnect, Connectable, GuidedSetup, Pod, PodConnector, Surface } from "@/data";
import { failureOf } from "@/data/pod-connectors";
import { ProblemHere, ProblemLine } from "@/connect/connection-problem";
import { Problem } from "@/outcomes/calm";
import { StepHead } from "@/ui/form";
import { CheckIcon, CloseIcon, CopyIcon, ExternalIcon, RefreshIcon, WarningIcon } from "@/ui/icons";
import { KnownSender } from "@/session/mobile-verification";
import { helloLink, placeKeyOf } from "@/arrival/reach";
import { OwnBot } from "./own-bot";
import { ChannelIcon, channelKey, channelName } from "./channels";
import { ConfirmDialog } from "./confirm-dialog";
import { completionPath, hereWith, openAuthorization, useConnectOutcome } from "@/connect/round-trip";
import { connectorProblem, type ConfigSource } from "@/connect/install";
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

/** Statuses that say a connected place is not answering yet. */
const NOT_ANSWERING = new Set(["PENDING_ADMIN_CONSENT", "NEEDS_SETUP", "ERROR", "INACTIVE"]);

/** The address, large, in figures, with a copy button beside it: the thing
 *  the whole sheet is for. */
function Handle({ text }: { text: string }) {
    const [copied, setCopied] = useState(false);
    const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
    useEffect(() => () => clearTimeout(timer.current), []);
    return (
        <span className="arp__handle">
            <span>{text}</span>
            <button
                type="button"
                className="arp__copy kd-copy"
                aria-label={copied ? "Copied" : "Copy " + text}
                onClick={() => {
                    clearTimeout(timer.current);
                    copyText(text)
                        .then(() => {
                            setCopied(true);
                            timer.current = setTimeout(() => setCopied(false), 1600);
                        })
                        .catch(() => undefined);
                }}
            >
                {copied ? <CheckIcon size={14} /> : <CopyIcon size={14} />}
            </button>
            <span className="sr-only" role="status">{copied ? "Copied" : ""}</span>
        </span>
    );
}

/** What this will cost, in words, before anything is clicked. */
const COST: Record<string, string> = {
    instant: "One tap",
    guided: "About a minute",
    account: "Needs your account",
    unavailable: "Not available",
};

/** The cost line, where the generic one would mislead. A Telegram bot of
 *  somebody's own is a token they paste, not an account they sign in to. */
function costOf(entry: Connectable): string {
    if (entry.effort === "account" && entry.platform === "TELEGRAM" && fields(entry.credentialSchema).length > 0) {
        return "Needs a bot token";
    }
    return COST[entry.effort];
}

/** The machine's name when the reader is at it and can fix things there, and
 *  null anywhere else — see `unavailableNote`. */
function useFixableHere(): string | null {
    const machine = useThisComputer();
    return useThisMacAvailability() === "shown" ? machine : null;
}

/** What to press for a channel that cannot connect here: Sharing for the ones
 *  that need a public link, the channel's own form for the rest. Nothing at
 *  all off this machine, where neither is the reader's to change. */
function Unblock({ entry, machine }: { entry: Connectable; machine: string | null }) {
    if (!needsSharing(entry)) return <SetUpOnThisMac form={credentialFormForChannel(entry.platform)} force />;
    if (!machine) return null;
    return <button type="button" className="b b--s b--secondary" onClick={() => openSettings("this-mac-sharing")}>Open Sharing</button>;
}

/** A place it answers now, drawn as the way in draws one (`arp`, from
 *  `arrival/reach-step.tsx`), so a place looks the same the first time and
 *  every time after: its logo, its name, whether it is on, and under that the
 *  address, large and copyable. Where the app can open a chat with a hello
 *  already typed (WhatsApp, Telegram), "Say hi" does, with a QR code beside
 *  it for the phone in your pocket. Disconnecting asks first. */
function ConnectedRow({ surface, pod, dropping, dropError, onDrop, onManage }: {
    surface: Surface;
    pod: Pod;
    dropping: boolean;
    dropError: string | null;
    onDrop: (name: string) => Promise<unknown>;
    onManage: () => void;
}) {
    const [asking, setAsking] = useState(false);
    const name = channelName(surface.platform);
    const key = channelKey(surface.platform);
    const address = (key === "EMAIL" ? surface.email || surface.handle : surface.handle).trim();
    const answering = surface.active && !NOT_ANSWERING.has(surface.status ?? "");
    const place = placeKeyOf(surface.platform);
    const link = answering && surface.mine && place && place !== "EMAIL" ? helloLink(place, address, pod.name) : null;
    const hint = key === "EMAIL" ? " · nothing to turn on" : key === "SLACK" ? " · @mention " + pod.name : link ? " · say hi to start" : "";
    return (
        <li className="arp__place" data-state={answering ? "on" : "off"}>
            <div className="arp__row">
                <span className="arp__logo"><ChannelIcon platform={surface.platform} size={22} /></span>
                <span className="arp__said">
                    <span className="arp__name">{name}</span>
                    <span className="arp__line">
                        {answering
                            ? <><span className="arp__on"><i />On</span>{hint}</>
                            : <span className="arp__missing"><WarningIcon size={13} aria-hidden="true" />{surfaceStatus(surface.status, surface.active)}</span>}
                        {/* A surface on this pod that answers as a different
                            agent. Drawn like the pod's own, it says "Marketing
                            is on Telegram" about a bot that is not Marketing. */}
                        {!surface.mine && " · " + (surface.agentName ? "answers as " + surface.agentName : "answers under another name")}
                    </span>
                </span>
                <button type="button" className="b b--s b--secondary" onClick={onManage}>Manage</button>
            </div>
            <div className="arp__body" data-qr={link ? "" : undefined}>
                <div className="arp__left">
                    {address && <Handle text={address} />}
                    {link && (
                        <span className="arp__acts">
                            <a className="b b--m b--primary" href={link} target="_blank" rel="noreferrer">Say hi on {name}</a>
                        </span>
                    )}
                    {key === "EMAIL" && surface.mine && (
                        <span className="kd-small">Write from the address you signed in with and I’ll answer. Mail from anyone else waits for you to see.</span>
                    )}
                    {/* The address above is only half of a working channel. On
                        WhatsApp the sender's number is the only identity a
                        message carries, so a member with no number on their
                        profile writes to the Lemma they were just given and is
                        answered by a sign-up link. Said here, under the
                        address, rather than discovered there. */}
                    {surface.mine && key === "WHATSAPP" && <KnownSender />}
                    <span className="kd-place__acts">
                        <button
                            type="button"
                            className="b b--s b--secondary b--red"
                            onClick={() => setAsking(true)}
                            aria-label={"Disconnect " + name + " from " + pod.name}
                        >
                            Disconnect
                        </button>
                    </span>
                </div>
                {link && (
                    <span className="arp__qr">
                        <span className="verify__qr-paper"><QRCode value={link} size={104} bgColor="#ffffff" fgColor="#111111" /></span>
                        <span>Or scan with your phone</span>
                    </span>
                )}
            </div>
            {asking && (
                <ConfirmDialog
                    title={"Disconnect " + name + "?"}
                    line={"People who write to " + (address || name) + " stop reaching " + pod.name + "."}
                    confirm="Disconnect"
                    working="Disconnecting…"
                    danger
                    busy={dropping}
                    problem={dropError}
                    onConfirm={() => { onDrop(surface.name).then(() => setAsking(false), () => undefined); }}
                    onClose={() => setAsking(false)}
                />
            )}
        </li>
    );
}

/** How long the two setup polls below keep asking once every ask fails.
 *
 *  Both poll every 2.5 s while somebody finishes a step somewhere else, and
 *  both used to catch a failure, say so, and ask again — forever, for as long
 *  as the sheet stayed open. Eight in a row is twenty seconds of an API that
 *  is not answering, which is long enough to stop and hand the retry back to
 *  the person. */
const POLL_EVERY_MS = 2500;
const GIVE_UP_AFTER_FAILURES = 8;
/** An authorisation nobody finished in this long is not going to be. */
const ACCOUNT_WAIT_MS = 15 * 60_000;

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
        let failures = 0;
        const tick = window.setInterval(() => {
            /* `failed` reads the clock only when this renders, and a poll that
               keeps failing sets the same error and never re-renders it. So
               expiry is checked here, and a copy of the same setup is enough
               to make the render notice. */
            if (setup.expiresAt && Date.parse(setup.expiresAt) < Date.now()) {
                stop = true;
                window.clearInterval(tick);
                setSetup((current) => current && { ...current });
                return;
            }
            source
                .checkGuided(pod.id, setup.setupId)
                .then((next) => {
                    if (stop) return;
                    failures = 0;
                    setSetup(next);
                    if (next.status === "READY" || next.status === "COMPLETE") onDone();
                })
                .catch(() => {
                    if (stop) return;
                    failures += 1;
                    if (failures < GIVE_UP_AFTER_FAILURES) {
                        setError("Could not check setup. We will try again.");
                        return;
                    }
                    stop = true;
                    window.clearInterval(tick);
                    setError("We still can’t check on this setup. Reload the page to look again.");
                });
        }, POLL_EVERY_MS);
        return () => {
            stop = true;
            window.clearInterval(tick);
        };
    }, [setup, ready, failed, pod.id, onDone]);

    if (!setup) {
        return (
            <div className="guided">
                <button
                    type="button"
                    className="b b--m b--primary"
                    disabled={starting}
                    onClick={() => {
                        setStarting(true);
                        setError(null);
                        source
                            .startGuided(pod.id, "TELEGRAM")
                            .then(next => { setSetup(next); if (next.status === "COMPLETE" || next.status === "READY") onDone(); })
                            .catch((problem) =>
                                setError(connectorProblem(problem, "That could not be started.")),
                            )
                            .finally(() => setStarting(false));
                    }}
                >
                    {starting ? "Starting…" : "Make a bot of its own"}
                </button>
                {error && <Problem>{error}</Problem>}
            </div>
        );
    }

    if (failed) {
        return (
            <div className="guided">
                <Problem>{setup.error || "This setup expired or failed."}</Problem>
                <button type="button" className="b b--m b--secondary" onClick={() => { setSetup(null); setError(null); }}>Start again</button>
            </div>
        );
    }

    if (ready) {
        return (
            <p className="guided guided--done">
                <CheckIcon size={15} /> {setup.botUsername} is live.
            </p>
        );
    }

    return (
        <div className="guided">
            <a className="b b--m b--primary" href={setup.launchUrl} target="_blank" rel="noreferrer">
                Open Telegram <ExternalIcon size={14} />
            </a>
            <p>
                {setup.managerBot} will ask you for a name, then make the bot. This page notices when it is done —
                you can leave it open.
            </p>
            {error && <Problem>{error}</Problem>}
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
            " is already taken elsewhere in this organization."
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
    bind,
    configSource,
}: {
    entry: Connectable;
    pod: Pod;
    onDone: () => void;
    /** What to do with the account once it exists. A surface on it, unless
     *  told otherwise — shared into the pod as a SHARED connector, since a
     *  surface answers as the one account for everyone. Slack instead makes
     *  Lemma's own app the workspace's manager. */
    bind?: (accountId: string) => Promise<unknown>;
    /** Which kind of install to authorise against, when it matters. */
    configSource?: ConfigSource;
}) {
    const attach = bind ?? ((accountId: string) => source.connectOwnAccount(pod.id, entry.platform, accountId));
    const [link, setLink] = useState<AccountConnect | null>(null);
    const [stage, setStage] = useState<"idle" | "starting" | "waiting" | "binding">("idle");
    const [error, setError] = useState<string | null>(null);
    const name = entry.title || channelName(entry.platform);
    const [pendingAccount, setPendingAccount] = useState<string | null>(null);
    /* The account the provider's tab named on its way back. The only answer
       for an account that was already fine and was simply signed into again —
       nothing about it changes that a poll could notice. */
    const [named, setNamed] = useState<string | null>(null);
    useConnectOutcome((outcome) => {
        if (outcome.connect === "connected" && outcome.account) setNamed(outcome.account);
        else if (outcome.connect === "error") {
            /* The provider said no. Back to the start, which is also what ends
               the poll below: nothing it could find would change that answer. */
            setStage("idle");
            setLink(null);
            setError(outcome.reason || "The account was not connected.");
        }
    }, () => undefined);

    useEffect(() => {
        if (!link || stage !== "waiting") return;
        let stop = false;
        let failures = 0;
        const deadline = Date.now() + ACCOUNT_WAIT_MS;
        /* Back to the Connect button, with the reason beside it — the waiting
           view has nowhere to show one. */
        const giveUp = (reason: string) => {
            stop = true;
            window.clearInterval(tick);
            setStage("idle");
            setLink(null);
            setError(reason);
        };
        const look = () => named
            ? Promise.resolve(named)
            /* Scoped to the install this authorisation ran against, so an
               account on another install of the same connector is not it. */
            : source.findAccount(pod.orgId, entry.connectorId, link.before, link.authConfigId);
        const tick = window.setInterval(() => {
            if (Date.now() > deadline) {
                giveUp("That took too long. Connect again to start over.");
                return;
            }
            look()
                .then(async (accountId) => {
                    if (stop) return;
                    failures = 0;
                    if (!accountId) return;
                    stop = true;
                    window.clearInterval(tick);
                    setStage("binding");
                    setPendingAccount(accountId);
                    try {
                        await attach(accountId);
                        onDone();
                    } catch (problem) {
                        setStage("idle");
                        setLink(null);
                        setError(problem instanceof Error ? problem.message : "That account could not be used.");
                    }
                })
                .catch((problem) => {
                    if (stop) return;
                    failures += 1;
                    const reason = problem instanceof Error ? problem.message : "That account could not be used.";
                    if (failures >= GIVE_UP_AFTER_FAILURES) giveUp(reason);
                    else setError(reason);
                });
        }, POLL_EVERY_MS);
        return () => {
            stop = true;
            window.clearInterval(tick);
        };
    }, [link, stage, named, pod.orgId, pod.id, entry.connectorId, entry.platform, onDone]);

    if (pendingAccount && stage !== "binding") return <div className="guided">
        {error && <Problem>{error}</Problem>}
        <button type="button" className="b b--m b--secondary" onClick={async () => {
            setStage("binding"); setError(null);
            try { await attach(pendingAccount); onDone(); }
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
                {/* A button, not a `noreferrer` link: the finished tab reports
                    back to its opener and closes, rather than loading the app
                    a second time inside itself. */}
                <button type="button" className="b b--m b--primary" onClick={() => openAuthorization(link.authorizeUrl, name)}>
                    Authorise {name} <ExternalIcon size={14} />
                </button>
                <p>{name} asks whether Lemma may act for you. This page notices when you are done.</p>
                <span className="guided__wait"><RefreshIcon size={13} /> Waiting for {name}…</span>
            </div>
        );
    }

    return (
        <div className="guided">
            <button
                type="button"
                className="b b--m b--primary"
                disabled={stage === "starting"}
                onClick={() => {
                    setStage("starting");
                    setError(null);
                    source
                        .startAccount(
                            pod.orgId, entry.connectorId, undefined,
                            /* Back to this sheet. It polls for the account
                               anyway; this is where the tab lands. */
                            completionPath(hereWith({ reach: "1" })),
                            undefined,
                            configSource,
                        )
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
                            setError(connectorProblem(problem, "That could not be started."));
                        });
                }}
            >
                {/* Just "Connect": the row's own heading already says which
                    platform, and "Connect Microsoft Teams" was wide enough to
                    squeeze the description into a three-line column. */}
                {stage === "starting" ? "Connecting…" : "Connect"}
            </button>
            {error && <Problem>{error}</Problem>}
        </div>
    );
}

/** A place it could answer, as the same row a connected one is: its logo,
 *  what it would take in a few words, and the one button that starts it.
 *  One tap turns a shared number or address on in place; anything needing
 *  more opens its own steps in the sheet. Where this server cannot do it, the
 *  row stays, dimmed, saying why and who can fix it. */
function ConnectRow({
    entry,
    busy,
    onConnect,
    connectors,
    onUseConnector,
    onFocus,
    elsewhere,
}: {
    entry: Connectable;
    busy: boolean;
    onConnect: (platform: string) => void;
    /** The pod's SHARED connectors for this platform's connector. */
    connectors: PodConnector[];
    onUseConnector: (entry: Connectable, podConnector: string) => void;
    onFocus: (entry: Connectable) => void;
    /** Another teammate in this organization already answers here. */
    elsewhere: boolean;
}) {
    const name = entry.title || channelName(entry.platform);
    /* A connector this space already shares is not something to make
       somebody authorise again — a surface answers as the one account a
       SHARED connector runs as. But only worth offering when nothing else is
       already answering on this platform: an account answers under one name
       org-wide, so if another teammate is on Slack the odds are this account
       is the one they are on, and "Ready now" would be a button whose only
       outcome is a 409. The backend is the authority and still gets the last
       word; this just stops the UI promising ahead of it. */
    const held = entry.effort === "account" ? connectorsFor(connectors, entry.connectorId, "SHARED").find((one) => one.status === "READY") ?? null : null;
    /* Not for Slack: an account there is Lemma's app or a teammate's own, and
       neither is one to hand this teammate. Lemma's app manages the
       workspace; this teammate gets an app of its own, from the focused view. */
    const ready = elsewhere || entry.platform === "SLACK" ? null : held;
    const guiding = entry.effort === "guided";
    const machine = useFixableHere();
    /* Said instead of the blurb: what the channel is matters less than why it
       cannot be had here, and what to do about it. */
    const blocked = unavailableNote(entry, machine);
    const note = blocked ?? why(entry);

    const cost = ready ? COST.instant : elsewhere && held ? "Already in use" : costOf(entry);
    const action = blocked ? null
        : busy ? <span className="arp__spin" role="status" aria-label={entry.effort === "instant" ? "Turning on" : "Connecting"} />
            : guiding ? <button type="button" className="b b--s b--secondary" onClick={() => onFocus(entry)}>Set up</button>
                : entry.effort === "instant" ? <button type="button" className="b b--s b--secondary" onClick={() => onConnect(entry.platform)}>Turn on</button>
                    : ready ? <button type="button" className="b b--s b--secondary" onClick={() => onUseConnector(entry, ready.slug)}>Connect</button>
                        : entry.effort === "account" || entry.effort === "unavailable"
                            ? <button type="button" className="b b--s b--secondary" onClick={() => onFocus(entry)}>{elsewhere && held ? "Give it its own" : "Set up"}</button>
                            : null;
    /* A choice, not only a fallback: a bot carrying this teammate's own name
       is a different — often better — outcome than sharing one. Offered
       wherever it is possible, which is the guided platforms and Slack; on
       Slack it is also the ONLY way for a second teammate, since a connected
       account is claimable once per organization. Not when the row's own
       button already says exactly this. */
    const ownWay = (entry.guided || OWN_BOT.has(entry.platform) || fields(entry.credentialSchema).length > 0) && !guiding && !(elsewhere && held) && !blocked;

    /* Not here: said in the row, plainly, with what can be done about it —
       and the place still shown, so nobody has to guess it exists. */
    return (
        <li className="arp__place" data-state={blocked ? "missing" : "off"}>
            <div className="arp__row">
                <span className="arp__logo"><ChannelIcon platform={entry.platform} size={22} /></span>
                <span className="arp__said">
                    <span className="arp__name">{name}</span>
                    <span className="arp__line">
                        {blocked
                            ? <span className="arp__missing"><WarningIcon size={13} aria-hidden="true" />{blocked}</span>
                            : busy
                                ? entry.effort === "instant" ? "Turning on…" : "Connecting…"
                                : [cost, note].filter(Boolean).join(" · ")}
                    </span>
                </span>
                {action}
            </div>
            <div className="arp__body kd-place__more">
                {ready && !guiding && (
                    <span className="kd-small">
                        Using {ready.slug}{ready.sharedAccount ? " (" + ready.sharedAccount + ")" : ""}, already shared in this space.
                    </span>
                )}
                {elsewhere && held && !guiding && (
                    <span className="kd-small">
                        {name} is already in use elsewhere in this organization, so this one needs an identity of its own.
                    </span>
                )}
                {ownWay && (
                    <span className="kd-place__acts">
                        <button type="button" className="b b--s b--quiet" onClick={() => onFocus(entry)}>
                            Use your own account
                        </button>
                    </span>
                )}
                {blocked && <Unblock entry={entry} machine={machine} />}
            </div>
        </li>
    );
}

/** One channel, on its own, with one thing to do.
 *
 *  The list answers "where could this teammate be?"; it is the wrong shape for
 *  "and how do I set this one up", which is a few sentences and a single
 *  action. So picking a channel that needs real work replaces the list rather
 *  than nesting a second dialog inside it, without that old modal's habit of
 *  sending you somewhere else to do the work.
 *
 *  Drawn as every step in a sheet is (`StepHead`): the way back on the left,
 *  then the channel's mark beside what this step is and what doing it does,
 *  and under that the form, left-aligned and as wide as the sheet. */
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
    const machine = useFixableHere();
    /* The connector's sign-in app, where it would come from this computer. */
    const appForm = oauthFormForConnector(entry.connectorId) ?? credentialFormForChannel(entry.platform);
    const mark = <span className="kmark" aria-hidden="true"><ChannelIcon platform={entry.platform} size={20} /></span>;
    const head = (title: string, line?: ReactNode) => <StepHead onBack={onBack} mark={mark} title={title} line={line} />;

    /* No setup guide here any more: the one the API serves is written for
       whoever calls it — name a pod connector, POST the surface — and was
       shown to people deciding where a colleague should answer. */
    if ((custom || !entry.guided) && hasCredentials) {
        return (
            <div className="kstep">
                {entry.platform === "TELEGRAM"
                    ? head("Use a bot you already made", "Paste the token @BotFather gave you when you made it. Send it /token to see it again.")
                    : head("Connect your " + name + " account", pod.name + " answers what people send to this account.")}
                <SurfaceCredentials pod={pod} entry={entry} onDone={onDone} />
                {entry.guided && <button type="button" className="b b--s b--quiet" onClick={() => setCustom(false)}>Make a new bot instead</button>}
            </div>
        );
    }
    if (ownBot && canOwn) {
        return (
            <div className="kstep">
                {head(
                    "Give " + pod.name + " a Slack bot of its own",
                    taken
                        ? "That Slack account already answers under another name here, and one Slack app is one bot, so " + pod.name + " needs an app of its own."
                        : "One Slack app is one bot, so " + pod.name + " needs an app under its own name.",
                )}
                <OwnBot entry={entry} pod={pod} onDone={onDone} />
            </div>
        );
    }
    if (entry.platform === "SLACK" && entry.account) {
        return <SlackManagerStep entry={entry} pod={pod} head={head} onReady={() => setOwnBot(true)} />;
    }
    if (entry.guided) {
        return (
            <div className="kstep">
                {head("Give " + pod.name + " a bot of its own", "Lemma’s setup bot makes it for you and names it after " + pod.name + ". You won’t need to touch BotFather.")}
                <Guided pod={pod} onDone={onDone} />
                {hasCredentials && <button type="button" className="b b--s b--quiet" onClick={() => setCustom(true)}>Use a bot you already made</button>}
            </div>
        );
    }
    return (
        <div className="kstep">
            {head(
                "Connect " + name + " to " + pod.name,
                /* Only a Lemma-supplied app is certain to reach a consent
                   page; without one the sign-in needs an app registered
                   first, and promising the page was how "needs setting up by
                   an admin" arrived as a surprise. */
                (entry.hostedOAuth
                    ? name + " will ask whether Lemma may act for you. "
                    : name + " signs in through an app registered for this organization. ")
                + "Nothing is sent anywhere until you say so.",
            )}
            {!entry.hostedOAuth && (
                <SetUpOnThisMac
                    form={appForm}
                    force
                    lead={"Lemma has no " + name + " app on {machine} yet. Add one once and everyone here can connect."}
                />
            )}
            <SharedConnectors entry={entry} pod={pod} onDone={onDone} />
            {entry.account ? <Account entry={entry} pod={pod} onDone={onDone} /> : (
                machine ? (
                    /* On a local install the administrator is the person
                       reading, and the bot credentials are this computer's
                       to set. */
                    <SetUpOnThisMac form={credentialFormForChannel(entry.platform)} force lead={name + " isn’t set up on {machine} yet."} />
                ) : (
                    <p>{name} isn’t set up on this server yet. Whoever runs it can add it.</p>
                )
            )}
            {canOwn && (
                <button type="button" className="b b--s b--quiet" onClick={() => setOwnBot(true)}>
                    Or give {pod.name} a Slack bot of its own
                </button>
            )}
        </div>
    );
}

/** The space's own shared connectors for this platform, each a way to answer
 *  without authorising anything — or, with none, where to set one up.
 *
 *  A surface answers as one account for everyone who writes to it, so only a
 *  SHARED pod connector can carry one; a per-member connector is each
 *  person's own and is not offered. Every one is listed, with why it cannot
 *  be used yet when it cannot, rather than only the ready ones: a connector
 *  that exists but has no account shared is a different fix from none at
 *  all. */
function SharedConnectors({ entry, pod, onDone }: { entry: Connectable; pod: Pod; onDone: () => void }) {
    const name = entry.title || channelName(entry.platform);
    const connectors = useQuery({
        queryKey: ["pod-connectors", pod.id],
        queryFn: () => source.listPodConnectors(pod.id),
        staleTime: 60_000,
    });
    const use = useMutation({
        mutationFn: (slug: string) => source.connectPodConnector(pod.id, entry.platform, slug),
        onSuccess: onDone,
    });
    const shared = connectorsFor(connectors.data ?? [], entry.connectorId, "SHARED");
    if (connectors.isPending) return <p className="guided__wait" role="status"><RefreshIcon size={13} /> Looking for this space’s {name} connectors…</p>;
    if (connectors.isError) {
        return (
            <Problem onRetry={() => void connectors.refetch()} retrying={connectors.isFetching}>
                Couldn’t read this space’s {name} connectors.
            </Problem>
        );
    }
    if (shared.length === 0) {
        return (
            <p>
                This space has no shared {name} connector yet.{" "}
                <a className="linkish" href={connectionsPath(pod.id)}>Set one up in connections</a>
                {entry.account ? ", or connect an account of your own here." : "."}
            </p>
        );
    }
    return (
        <>
            <ul className="reachrows">
                {shared.map((one) => (
                    <li key={one.slug} className="reachrow">
                        <div className="reachrow__body">
                            <b>{one.slug}</b>
                            <span className="reachrow__note">
                                {statusNote(one) || (one.sharedAccount ? "Answers as " + one.sharedAccount : "Ready")}
                            </span>
                        </div>
                        <div className="reachrow__acts">
                            {one.status === "READY" ? (
                                <button type="button" className="b b--s b--secondary" disabled={use.isPending} onClick={() => use.mutate(one.slug)}>
                                    {use.isPending && use.variables === one.slug ? "Connecting…" : "Use"}
                                </button>
                            ) : (
                                <a className="b b--s b--secondary" href={connectionsPath(pod.id, one.slug)}>Fix in connections</a>
                            )}
                        </div>
                    </li>
                ))}
            </ul>
            {use.isError && <ProblemHere error={use.error} fallback="That connector could not be used." />}
        </>
    );
}

/** Lemma's own app in the workspace first, then this teammate's own bot.
 *
 *  Lemma's app manages a Slack workspace's teammates -- it is where people see
 *  them, add one, and hand one a channel -- and answers nobody. So connecting
 *  Slack is two steps the first time and one after: add Lemma to the
 *  workspace, then give this teammate an app of its own. */
function SlackManagerStep({ entry, pod, head, onReady }: {
    entry: Connectable;
    pod: Pod;
    head: (title: string, line?: ReactNode) => ReactNode;
    onReady: () => void;
}) {
    const managed = useQuery({
        queryKey: ["slack-managers", pod.id],
        queryFn: () => source.slackManagedWorkspaces(pod.id),
    });
    useEffect(() => {
        if (managed.data?.length) onReady();
    }, [managed.data, onReady]);
    if (managed.isPending || managed.data?.length) {
        return (
            <div className="kstep">
                {head("Connect Slack")}
                <p className="guided__wait" role="status"><RefreshIcon size={13} /> Checking your Slack workspace…</p>
            </div>
        );
    }
    return (
        <div className="kstep">
            {head(
                "Add Lemma to your Slack workspace",
                "Lemma’s app is where your workspace sees its Lemmas and adds them. It answers nobody itself. Then " + pod.teammate.name + " gets a Slack bot of its own.",
            )}
            <Account
                entry={entry}
                pod={pod}
                onDone={onReady}
                bind={(accountId) => source.bindSlackManager(pod.id, accountId)}
                /* Lemma's own app, never a teammate's own Slack app: those are
                   installs of the same connector, and the manager refuses them. */
                configSource="SYSTEM_DEFAULT"
            />
            <button type="button" className="b b--s b--quiet" onClick={onReady}>
                Skip this, and give {pod.teammate.name} its bot now
            </button>
        </div>
    );
}

/** Where to reach a Lemma, as the kit's sheet from the right edge.
 *
 *  It was a dialog in the middle of the screen, which hid the Lemma it was
 *  about. A sheet keeps the page in view while the places are turned on, and
 *  the page stays put: it slides in over a light veil and goes the same way.
 *  The Lemma says it, in the first person: these are the places you can reach
 *  it, and it answers where you write. */
export function ReachSheet({ pod, onClose }: { pod: Pod; onClose: () => void }) {
    return (
        <Sheet
            label={"Where to reach " + pod.name}
            title="Where you can reach me"
            line={"Nobody has to open Lemma to talk to " + pod.name + ". I answer right where you write."}
            onClose={onClose}
        >
            <ReachPanel pod={pod} onLeave={onClose} />
        </Sheet>
    );
}

/** The kit's sheet (`.ksheet`): a heading, a close button, and a body that
 *  scrolls. Escape, a click on the veil and the close button all close it;
 *  Tab stays inside while it is open, and focus goes back where it was.
 *
 *  A dialog opened over it — Disconnect's question — owns the keyboard: the
 *  shared `Modal` stops Escape before it gets here, and a key pressed with
 *  focus outside the sheet is not the sheet's. */
function Sheet({ label, title, line, onClose, children }: {
    label: string;
    title: string;
    line?: string;
    onClose: () => void;
    children: ReactNode;
}) {
    const panel = useRef<HTMLElement>(null);
    const close = useRef(onClose);
    close.current = onClose;
    const [mounted, setMounted] = useState(false);
    useEffect(() => setMounted(true), []);
    useEffect(() => {
        if (!mounted) return;
        const previous = document.activeElement as HTMLElement | null;
        panel.current?.focus();
        const onKey = (event: KeyboardEvent) => {
            const target = event.target as Node | null;
            if (target && target !== document.body && !panel.current?.contains(target)) return;
            if (event.key === "Escape") {
                event.preventDefault();
                close.current();
                return;
            }
            if (event.key !== "Tab") return;
            const focusable = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled]), select, textarea, [tabindex="0"]') ?? [])
                .filter((element) => element.getClientRects().length > 0);
            const first = focusable[0];
            const last = focusable.at(-1);
            if (!first) { event.preventDefault(); return; }
            if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last?.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        };
        document.addEventListener("keydown", onKey);
        return () => {
            document.removeEventListener("keydown", onKey);
            if (previous?.isConnected) previous.focus();
        };
    }, [mounted]);
    if (!mounted) return null;
    return createPortal(
        <>
            <div className="ksheet-veil" aria-hidden="true" onMouseDown={() => close.current()} />
            <aside className="ksheet" role="dialog" aria-modal="true" aria-label={label} tabIndex={-1} ref={panel}>
                <header className="ksheet__head">
                    <h2>{title}</h2>
                    {line && <p>{line}</p>}
                    <button type="button" className="ksheet__x b b--m b--quiet b--icon" onClick={() => close.current()} aria-label="Close">
                        <CloseIcon size={19} />
                    </button>
                </header>
                <div className="ksheet__body">{children}</div>
            </aside>
        </>,
        document.body,
    );
}

/** What the sheet holds — the channels it answers on, and the ones it could —
 *  without the sheet. Channels on About is a page of its own, and this is that
 *  page; the strip elsewhere opens the same thing in a sheet.
 *
 *  One list, every place in it: those it answers on first, then the ones one
 *  tap or a little setup away, then the ones this server cannot do yet, each
 *  saying why in its own row. `inline` is the page's copy, which has its own
 *  heading over it. */
export function ReachPanel({ pod, onLeave, inline = false }: { pod: Pod; onLeave?: () => void; inline?: boolean }) {
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
    const connectors = useQuery({
        queryKey: ["pod-connectors", pod.id],
        queryFn: () => source.listPodConnectors(pod.id),
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
        void queryClient.invalidateQueries({ queryKey: ["pod-connectors", pod.id] });
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
            else setCompletionError("Connection saved. Refresh to finish setup.");
        } catch { setCompletionError("Connection saved, but setup could not be loaded. Refresh to continue."); }
    };

    /* One tap turns a place on where it is: the row becomes its address,
       with the way to say hi, rather than sending the person into its
       settings before they have written to it once. Into the list now, so the
       row does not offer "Turn on" again while the list is read back. */
    const connect = useMutation({
        mutationFn: (platform: string) => source.connectSystem(pod.id, platform),
        onSuccess: (surface) => {
            queryClient.setQueryData<Surface[]>(["surfaces", 2, pod.id], (was) => was ? [...was.filter((one) => one.id !== surface.id), surface] : was);
            refresh();
        },
    });
    const [taken, setTaken] = useState<string | undefined>(undefined);
    const useConnector = useMutation({
        mutationFn: ({ entry, podConnector }: { entry: Connectable; podConnector: string }) =>
            source.connectPodConnector(pod.id, entry.platform, podConnector),
        onSuccess: (surface) => { refresh(); setManaging(surface); },
        onError: (problem, variables) => {
            /* Not a failure to report and stop at: the account is spoken for,
               and the answer is an identity of this teammate's own. */
            if (!isCredentialConflict(problem)) return;
            setTaken("elsewhere");
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
    /* What can be had here first; what this server cannot do yet after it,
       in the same order, as the way in lists them. */
    const machine = useFixableHere();
    const offered = (surfaces.isSuccess ? catalog.data ?? [] : []).filter((entry) => !already.has(channelKey(entry.platform)));
    const offer = [...offered.filter((entry) => !unavailableNote(entry, machine)), ...offered.filter((entry) => unavailableNote(entry, machine))];

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

    const failed = connect.error ?? (isCredentialConflict(useConnector.error) ? null : useConnector.error);

    return (
        <>
            {managing ? <SurfaceManage pod={pod} surface={managing} onBack={() => setManaging(null)} onSaved={() => { refresh(); setManaging(null); }} onLeave={onLeave} /> : focus ? (
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
            <div className={inline ? "kd-reach kd-reach--inline" : "kd-reach"}>
                {(catalog.isPending || surfaces.isPending) && <p className="kd-small" role="status">Finding where you can reach {pod.name}…</p>}

                {(on.length > 0 || offer.length > 0) && (
                    <ul className="arp" aria-label={"Where to reach " + pod.name}>
                        {on.map((surface) => (
                            <ConnectedRow
                                key={surface.id}
                                surface={surface}
                                pod={pod}
                                dropping={drop.isPending && drop.variables === surface.name}
                                dropError={drop.isError && drop.variables === surface.name ? failureOf(drop.error, "Couldn’t disconnect it.").message : null}
                                onDrop={(name) => drop.mutateAsync(name)}
                                onManage={() => setManaging(surface)}
                            />
                        ))}
                        {offer.map((entry) => (
                            <ConnectRow
                                key={entry.platform}
                                entry={entry}
                                busy={
                                    (connect.isPending && connect.variables === entry.platform) ||
                                    (useConnector.isPending && useConnector.variables?.entry.platform === entry.platform)
                                }
                                onConnect={(platform) => connect.mutate(platform)}
                                onUseConnector={(chosen, podConnector) => useConnector.mutate({ entry: chosen, podConnector })}
                                onFocus={setFocus}
                                elsewhere={heldElsewhere.has(channelKey(entry.platform))}
                                connectors={connectors.data ?? []}
                            />
                        ))}
                    </ul>
                )}

                {surfaces.isSuccess && catalog.isSuccess && on.length === 0 && offer.length === 0 && (
                    <p className="kd-small">There is nowhere to reach {pod.name} on this server yet. Talk to it inside Lemma.</p>
                )}
                {catalog.isError && (
                    <p className="kd-problem" role="alert">
                        Couldn’t read where else it could answer.{" "}
                        <button type="button" className="b b--s b--secondary" onClick={() => void catalog.refetch()}><RefreshIcon size={15} /> Try again</button>
                    </p>
                )}
                {surfaces.isError && (
                    <p className="kd-problem" role="alert">
                        Couldn’t read where it answers now.{" "}
                        <button type="button" className="b b--s b--secondary" onClick={() => void surfaces.refetch()}><RefreshIcon size={15} /> Try again</button>
                    </p>
                )}
                {completionError && (
                    <p className="kd-problem" role="alert">
                        {completionError}{" "}
                        <button type="button" className="b b--s b--secondary" onClick={() => { refresh(); setCompletionError(null); }}>Refresh</button>
                    </p>
                )}
                {failed && <ProblemLine className="kd-problem" error={failed} fallback="Couldn’t update this." />}
            </div>
            )}
        </>
    );
}
