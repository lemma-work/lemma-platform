"use client";

import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import QRCode from "react-qr-code";
import { useQueryClient } from "@tanstack/react-query";
import { source, type Group, type Pod, type Surface } from "@/data";
import {
    answering,
    arrivedSince,
    inviteMailto,
    sayAnswering,
    sayAnsweringFully,
    slackInvite,
    whatsappShareUrl,
    WHATSAPP_TITLE_MAX,
} from "@/data/groups";
import { groupTitle, linkShown } from "@/data/surface-groups";
import { copyText } from "@/desktop/clipboard";
import { openExternalWhenReady } from "@/desktop/open-external";
import { isForbidden } from "@/session/auth-state";
import { useMe } from "@/session/use-me";
import { ChannelIcon } from "@/shell/channels";
import { Modal } from "@/shell/modal";
import { CheckIcon, CopyIcon, ExternalIcon } from "@/ui/icons";
import { groupsKey, useGroups } from "./group-queries";

/** Bringing the space's bot into a group, from Lemma — one sheet per
 *  platform, because each platform lets a bot in its own way. WhatsApp: the
 *  bot's number makes the group and you share its link. Telegram: one tap in
 *  Telegram adds the bot to a group you pick. Slack: you invite it in the
 *  channel. Each sheet waits, visibly, for the group to arrive, and ends on
 *  the way into it. */

export type TelegramLink = { url: string; expiresAt: string };

export type GroupSheet =
    | { kind: "whatsapp"; surface: Surface }
    /** `started`: the link, already on its way from a click elsewhere — the
     *  tab it opens has to be opened inside that click. */
    | { kind: "telegram"; surface: Surface; started?: Promise<TelegramLink> }
    | { kind: "slack"; surface: Surface };

/** Ask for a Telegram add-to-group link and open it, from inside a click.
 *  A browser blocks a tab opened after an await, so the tab is opened now
 *  and pointed at the link when it arrives. */
export function openTelegramLink(podId: string, surfaceName: string): Promise<TelegramLink> {
    const link = source.groupLink(podId, surfaceName);
    void openExternalWhenReady(link.then((made) => made.url)).catch(() => undefined);
    return link;
}

export function GroupSheets({ pod, bot = pod.name, sheet, onClose, onOpenGroup }: {
    pod: Pod;
    /** Who answers in the group: the space, unless the channel is an
     *  agent's own. */
    bot?: string;
    sheet: GroupSheet;
    onClose: () => void;
    onOpenGroup: (group: Group) => void;
}) {
    const open = (group: Group) => { onClose(); onOpenGroup(group); };
    if (sheet.kind === "whatsapp") return <StartWhatsApp pod={pod} bot={bot} surface={sheet.surface} onClose={onClose} onOpenGroup={open} />;
    if (sheet.kind === "telegram") return <AddTelegram pod={pod} bot={bot} surface={sheet.surface} started={sheet.started} onClose={onClose} onOpenGroup={open} />;
    return <AddSlack pod={pod} bot={bot} surface={sheet.surface} onClose={onClose} onOpenGroup={open} />;
}

/* ── shared pieces ─────────────────────────────────────────────────── */

function statusOf(error: unknown): number | null {
    const code = (error as { statusCode?: unknown } | null)?.statusCode;
    return typeof code === "number" ? code : null;
}

function messageOf(error: unknown): string | null {
    return error instanceof Error && error.message.trim() ? error.message.trim() : null;
}

/** Copy, with the word changing to say it worked — or that it did not, in
 *  which case the text beside it is there to select. */
function CopyPill({ text, label, quiet = false }: { text: string; label: string; quiet?: boolean }) {
    const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
    const timer = useRef<number | undefined>(undefined);
    useEffect(() => () => window.clearTimeout(timer.current), []);
    return (
        <>
            <button
                type="button"
                className={(quiet ? "ghost-pill" : "pill-button") + " gsheet__copy"}
                onClick={async () => {
                    window.clearTimeout(timer.current);
                    try { await copyText(text); setState("copied"); } catch { setState("failed"); }
                    timer.current = window.setTimeout(() => setState("idle"), 1800);
                }}
            >
                {state === "copied" ? <CheckIcon size={15} aria-hidden="true" /> : <CopyIcon size={15} aria-hidden="true" />}
                {state === "copied" ? "Copied" : state === "failed" ? "Couldn’t copy" : label}
            </button>
            <span className="sr-only" role="status">{state === "copied" ? "Copied" : state === "failed" ? "Could not copy. Select the text and copy it." : ""}</span>
        </>
    );
}

/** Something the sheet is waiting on, said while it waits. */
function Waiting({ children }: { children: ReactNode }) {
    return (
        <div className="gsheet__status" role="status">
            <span className="gsheet__dots" aria-hidden="true"><i /><i /><i /></span>
            <span>{children}</span>
        </div>
    );
}

/** The group arrived: who answers there, and the way in. */
function Arrived({ pod, group, onOpen }: { pod: Pod; group: Group; onOpen: () => void }) {
    const me = useMe();
    return (
        <div className="gsheet__status gsheet__status--done" role="status">
            <CheckIcon size={16} aria-hidden="true" />
            <span className="gsheet__status-text">
                {groupTitle(group)} is connected. {sayAnsweringFully(answering(group, me, pod.members), pod.name)}.
            </span>
            <button type="button" className="pill-button" onClick={onOpen}>Open group</button>
        </div>
    );
}

/** A group that turns up on this channel after the sheet opened. The list
 *  is read every few seconds while `watching`; what was there on first
 *  sight is the baseline, so a group that was already in it never counts. */
function useArrival(podId: string, surfaceName: string, watching: boolean): Group | null {
    const [found, setFound] = useState<Group | null>(null);
    const groups = useGroups(podId, watching && !found ? 3_000 : false);
    const before = useRef<Set<string> | null>(null);
    if (before.current === null && groups.data) before.current = new Set(groups.data.map((group) => group.id));
    const arrived = before.current && groups.data ? arrivedSince(before.current, groups.data, surfaceName) : null;
    useEffect(() => { if (!found && arrived) setFound(arrived); }, [arrived, found]);
    return found;
}

/* ── WhatsApp: the number makes the group ──────────────────────────── */

/** How long to keep asking WhatsApp for a group it has not confirmed. It
 *  usually answers in a second or two. */
const CONFIRM_EVERY_MS = 2_000;
const CONFIRM_FOR_MS = 60_000;

function startProblem(error: unknown, bot: string): string {
    const status = statusOf(error);
    if (status === 502) return "WhatsApp would not start the group. Try again shortly.";
    if (isForbidden(error)) return "Only someone who can change " + bot + " can start a group on this number.";
    if (status === 409 || status === 422) return messageOf(error) ?? "WhatsApp would not start the group.";
    return "That didn’t work. Try again.";
}

function StartWhatsApp({ pod, bot, surface, onClose, onOpenGroup }: {
    pod: Pod;
    bot: string;
    surface: Surface;
    onClose: () => void;
    onOpenGroup: (group: Group) => void;
}) {
    const cache = useQueryClient();
    const me = useMe();
    const ids = useId();
    const [title, setTitle] = useState("");
    const [outsiders, setOutsiders] = useState(true);
    const [busy, setBusy] = useState(false);
    const [problem, setProblem] = useState<string | null>(null);
    const [group, setGroup] = useState<Group | null>(null);
    const [slow, setSlow] = useState(false);
    const [qr, setQr] = useState(false);
    /* Into the name at once. After the dialog's own focus, which lands on the
       panel: a parent's effect runs after its children's. */
    const nameField = useRef<HTMLInputElement>(null);
    useEffect(() => { nameField.current?.focus(); }, []);

    /* Asked every two seconds until WhatsApp confirms the group and its link
       arrives. A minute is long enough to stop asking and say so calmly: the
       group is not lost, it is in the list, and it fills in there. */
    useEffect(() => {
        if (!group || group.inviteLink) return;
        let stop = false;
        const since = Date.now();
        const tick = window.setInterval(() => {
            if (Date.now() - since > CONFIRM_FOR_MS) {
                stop = true;
                window.clearInterval(tick);
                setSlow(true);
                return;
            }
            source.getGroup(pod.id, group.id).then((next) => {
                if (stop || !next.inviteLink) return;
                stop = true;
                window.clearInterval(tick);
                setGroup(next);
                void cache.invalidateQueries({ queryKey: groupsKey(pod.id) });
            }).catch(() => undefined);
        }, CONFIRM_EVERY_MS);
        return () => { stop = true; window.clearInterval(tick); };
    }, [group, pod.id, cache]);

    const start = async (event: FormEvent) => {
        event.preventDefault();
        /* This sheet is drawn through a portal, and React carries a submit up
           the component tree, not the page: opened from a bot's channel
           settings, it reached that form too, which saved and closed — taking
           this sheet with it before the link could appear. It ends here. */
        event.stopPropagation();
        const name = title.trim();
        if (!name || busy) return;
        setBusy(true);
        setProblem(null);
        try {
            const made = await source.startGroup(pod.id, { surfaceName: surface.name, title: name, answersOutsiders: outsiders });
            setGroup(made);
            void cache.invalidateQueries({ queryKey: groupsKey(pod.id) });
        } catch (error) {
            setProblem(startProblem(error, bot));
        } finally {
            setBusy(false);
        }
    };

    const mark = <ChannelIcon platform="WHATSAPP" size={32} />;

    if (group?.inviteLink) {
        const name = groupTitle(group);
        const link = group.inviteLink;
        return (
            <Modal mark={mark} title={name + " is ready"} onClose={onClose}>
                <div className="gsheet">
                    <div className="gsheet__block">
                        <span className="gsheet__label">Share this link with the people who should join</span>
                        <div className="gsheet__code">
                            <span className="gsheet__code-text" title={link}>{linkShown(link)}</span>
                            <CopyPill text={link} label="Copy link" />
                        </div>
                        <div className="gsheet__acts">
                            <a className="ghost-pill" href={whatsappShareUrl(name, link)} target="_blank" rel="noreferrer">
                                Send on WhatsApp <ExternalIcon size={13} aria-hidden="true" />
                            </a>
                            <button type="button" className="ghost-pill" aria-expanded={qr} onClick={() => setQr((was) => !was)}>
                                {qr ? "Hide QR code" : "Show QR code"}
                            </button>
                            <a className="ghost-pill" href={inviteMailto(name, link)}>Email it</a>
                        </div>
                        {qr && (
                            <div className="verify__qr gsheet__qr">
                                <div className="verify__qr-paper">
                                    <QRCode value={link} size={148} level="M" bgColor="#fffefa" fgColor="#20211f" title={"Scan to join " + name} />
                                </div>
                                <small>Point a phone’s camera at it to join.</small>
                            </div>
                        )}
                    </div>
                    <dl className="gsheet__facts">
                        <div>
                            <dt>{bot} answers</dt>
                            <dd>when someone says “{bot}” or replies to it</dd>
                        </div>
                        <div>
                            <dt>People outside {pod.name}</dt>
                            <dd>
                                {sayAnswering(answering(group, me, pod.members), pod.name)}
                                {" · "}
                                <button type="button" className="gsheet__inline" onClick={() => onOpenGroup(group)}>Change</button>
                            </dd>
                        </div>
                    </dl>
                    <p className="gsheet__fine">Anyone with the link can join, so send it only to the people you mean.</p>
                    <div className="gsheet__foot">
                        <button type="button" className="ghost-pill" onClick={onClose}>Done</button>
                        <button type="button" className="pill-button" onClick={() => onOpenGroup(group)}>Open group</button>
                    </div>
                </div>
            </Modal>
        );
    }

    if (group) {
        return (
            <Modal mark={mark} title={"Creating " + groupTitle(group)} onClose={onClose}>
                <div className="gsheet">
                    {slow ? (
                        <p className="gsheet__status" role="status">
                            WhatsApp is taking longer than usual. {groupTitle(group)} shows up in Groups once it is ready, so you can close this.
                        </p>
                    ) : (
                        <Waiting>WhatsApp is making the group. Its link appears here in a few seconds.</Waiting>
                    )}
                    <div className="gsheet__foot">
                        <button type="button" className="ghost-pill" onClick={onClose}>Close</button>
                        <button type="button" className="pill-button" onClick={() => onOpenGroup(group)}>Open group</button>
                    </div>
                </div>
            </Modal>
        );
    }

    return (
        <Modal mark={mark} title="Start a WhatsApp group" onClose={onClose}>
            <form className="gsheet" onSubmit={(event) => void start(event)}>
                <label className="gsheet__field">
                    <span className="gsheet__label">What is it for?</span>
                    <input
                        value={title}
                        onChange={(event) => setTitle(event.target.value)}
                        maxLength={WHATSAPP_TITLE_MAX}
                        placeholder="Acme × Northwind"
                        required
                        ref={nameField}
                        aria-describedby={ids + "-name"}
                    />
                    <span className="gsheet__hint" id={ids + "-name"}>This is the group’s name in WhatsApp. People see it when they join.</span>
                </label>
                <ol className="gsheet__steps">
                    <li>{bot} creates the group on <span className="gsheet__em">{surface.handle || "its WhatsApp number"}</span>.</li>
                    <li>You get a link. Send it to whoever should join. A group holds up to eight people.</li>
                    <li>Anyone in the group can ask {bot} by name.</li>
                </ol>
                <label className="gsheet__check">
                    <input type="checkbox" checked={outsiders} onChange={(event) => setOutsiders(event.target.checked)} />
                    <span>
                        <span className="gsheet__check-title">Answer people outside {pod.name}</span>
                        <span className="gsheet__hint">From what is Public. Anything else comes to you.</span>
                    </span>
                </label>
                {problem && <p className="gsheet__problem" role="alert">{problem}</p>}
                <div className="gsheet__foot">
                    <button type="button" className="ghost-pill" onClick={onClose}>Cancel</button>
                    <button type="submit" className="pill-button" disabled={busy || !title.trim()}>{busy ? "Starting…" : "Start group"}</button>
                </div>
            </form>
        </Modal>
    );
}

/* ── Telegram: one tap in Telegram adds the bot ────────────────────── */

function linkProblem(error: unknown, bot: string): string {
    if (isForbidden(error)) return "Only someone who can change " + bot + " can add it to a group.";
    const status = statusOf(error);
    if (status === 409 || status === 422) return (messageOf(error) ?? "Telegram can’t take that link right now") + ".";
    return "Couldn’t make the link. Try again.";
}

function AddTelegram({ pod, bot, surface, started, onClose, onOpenGroup }: {
    pod: Pod;
    bot: string;
    surface: Surface;
    started?: Promise<TelegramLink>;
    onClose: () => void;
    onOpenGroup: (group: Group) => void;
}) {
    const [link, setLink] = useState<TelegramLink | null>(null);
    const [asking, setAsking] = useState(Boolean(started));
    const [problem, setProblem] = useState<string | null>(null);
    const [showLink, setShowLink] = useState(false);
    const found = useArrival(pod.id, surface.name, Boolean(link) || asking);

    const settle = (made: Promise<TelegramLink>) => {
        setAsking(true);
        setProblem(null);
        made.then(setLink)
            .catch((error: unknown) => setProblem(linkProblem(error, bot)))
            .finally(() => setAsking(false));
        return made;
    };

    /* A link already on its way from the click that opened this sheet. */
    const took = useRef(false);
    useEffect(() => {
        if (!started || took.current) return;
        took.current = true;
        settle(started);
    });

    const openTelegram = () => { settle(openTelegramLink(pod.id, surface.name)); };
    const copyInstead = async () => {
        const current = link ?? await settle(source.groupLink(pod.id, surface.name)).catch(() => null);
        if (!current) return;
        setShowLink(true);
        try { await copyText(current.url); } catch { /* the link is on screen with its own Copy */ }
    };

    const step = found ? 4 : 1;
    return (
        <Modal mark={<ChannelIcon platform="TELEGRAM" size={32} />} title={"Add " + bot + " to a Telegram group"} onClose={onClose}>
            <div className="gsheet">
                <button type="button" className="pill-button gsheet__big" disabled={asking} onClick={openTelegram}>
                    {asking ? "Opening Telegram…" : "Open Telegram and pick a group"}
                </button>
                <ol className="gsheet__steps gsheet__steps--dots">
                    <li data-at={step === 1 || undefined} data-done={step > 1 || undefined}>Telegram asks which group. Pick one you run, or make a new one there.</li>
                    <li data-done={step > 2 || undefined}>{bot} joins and says hello, so everyone knows it is there.</li>
                    <li data-done={step > 3 || undefined}>The group shows up here, with you answering for anyone outside {pod.name}.</li>
                </ol>
                {problem && <p className="gsheet__problem" role="alert">{problem}</p>}
                {found ? <Arrived pod={pod} group={found} onOpen={() => onOpenGroup(found)} />
                    : link && <Waiting>Waiting for Telegram. Keep this open, or come back later.</Waiting>}
                {showLink && link && (
                    <div className="gsheet__code">
                        <span className="gsheet__code-text" title={link.url}>{linkShown(link.url)}</span>
                        <CopyPill text={link.url} label="Copy" quiet />
                    </div>
                )}
                <p className="gsheet__fine">On Telegram, {bot} can join a group but not start one. It hears the messages that mention it or reply to it.</p>
                <div className="gsheet__foot gsheet__foot--split">
                    <button type="button" className="gsheet__inline" onClick={() => void copyInstead()}>Copy the link instead</button>
                    <button type="button" className="ghost-pill" onClick={onClose}>Done</button>
                </div>
            </div>
        </Modal>
    );
}

/* ── Slack: invite it in the channel ───────────────────────────────── */

function AddSlack({ pod, bot, surface, onClose, onOpenGroup }: {
    pod: Pod;
    bot: string;
    surface: Surface;
    onClose: () => void;
    onOpenGroup: (group: Group) => void;
}) {
    const command = slackInvite(surface.handle, bot);
    const found = useArrival(pod.id, surface.name, true);
    return (
        <Modal mark={<ChannelIcon platform="SLACK" size={32} />} title={"Add " + bot + " to a Slack channel"} onClose={onClose}>
            <div className="gsheet">
                <div className="gsheet__block">
                    <span className="gsheet__label">In the channel, send this</span>
                    <div className="gsheet__code">
                        <code className="gsheet__code-text">{command}</code>
                        <CopyPill text={command} label="Copy" />
                    </div>
                    <span className="gsheet__hint">If Slack asks who answers there, pick {bot}.</span>
                </div>
                <dl className="gsheet__cases">
                    <div>
                        <dt>A channel inside your company</dt>
                        <dd>People who are not in {pod.name} get a private note inviting them in. The channel sees nothing.</dd>
                    </div>
                    <div>
                        <dt>A channel shared with another company</dt>
                        <dd>Their people are answered from what is Public, like any group, once someone here takes it on.</dd>
                    </div>
                </dl>
                {found ? <Arrived pod={pod} group={found} onOpen={() => onOpenGroup(found)} />
                    : <Waiting>Waiting for Slack. The channel shows up here after the next message in it.</Waiting>}
                <div className="gsheet__foot">
                    <button type="button" className="ghost-pill" onClick={onClose}>Done</button>
                </div>
            </div>
        </Modal>
    );
}
