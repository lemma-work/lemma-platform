"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import QRCode from "react-qr-code";
import { source, type Pod } from "@/data";
import { lemma } from "@/session/client";
import { KnownSender } from "@/session/mobile-verification";
import { captureEvent } from "@/site/analytics/client";
import type { BuiltPlugin } from "@/site/plugins/catalog";
import { listed } from "@/site/plugins/catalog";
import { originOf } from "@/thread/conversation-origin";
import { copyText } from "@/desktop/clipboard";
import { CopyIcon, CheckIcon, ExternalIcon, WarningIcon } from "@/ui/icons";
import { InstallStrip } from "./install-strip";
import type { InstallRun } from "./install-run";
import { heardOn, helloLink, placesFrom, type Place, type PlaceKey } from "./reach";
import { PlaceLogo } from "./stage";

const NAMES: Record<PlaceKey, string> = { EMAIL: "Email", WHATSAPP: "WhatsApp", TELEGRAM: "Telegram", SLACK: "Slack" };

/** What the Lemma on the left shows of this step: where it is on, and where
 *  your first hello was just heard. */
export interface ReachState {
    on: PlaceKey[];
    heard: PlaceKey | null;
}

/** "Write to me from anywhere." -- while the picked apps go in, the places
 *  the Lemma can be reached from: the address it already has, and what one
 *  tap turns on here. Writing to it from any of them is the moment this
 *  step is for: the screen hears it, says so, and the face reacts. With no
 *  place to offer on this server, the step is the apps going in alone. */
export function ReachStep({ pod, orgId, team, firstName, plugins, run, onChange, onDone }: {
    pod: Pod;
    orgId: string;
    team: boolean;
    firstName: string;
    plugins: readonly BuiltPlugin[];
    run: InstallRun;
    onChange: (state: ReachState) => void;
    onDone: () => void;
}) {
    const queryClient = useQueryClient();
    const connectable = useQuery({ queryKey: ["arrival-connectable", pod.id], queryFn: () => source.listConnectable(pod.id) });
    const [slackWaiting, setSlackWaiting] = useState(false);
    const surfaces = useQuery({
        queryKey: ["arrival-surfaces", pod.id],
        queryFn: () => source.listSurfaces(pod.id),
        refetchInterval: slackWaiting ? 5000 : false,
    });
    const slack = useQuery({ queryKey: ["arrival-slack"], queryFn: () => source.slackInstallAvailable(), staleTime: 5 * 60_000 });
    const places = useMemo(
        () => placesFrom({ connectable: connectable.data ?? [], surfaces: surfaces.data ?? [], slack: slack.data === true, team }),
        [connectable.data, surfaces.data, slack.data, team],
    );
    const reading = connectable.isPending || surfaces.isPending;
    const on = places.filter((one) => one.state === "on").map((one) => one.key);

    /* The first hello: conversations say where they were heard. */
    const since = useRef(Date.now());
    const [heard, setHeard] = useState<PlaceKey | null>(null);
    const listening = on.length > 0 && !heard;
    const recent = useQuery({
        queryKey: ["arrival-hello", pod.id],
        queryFn: async () => {
            const listed = await lemma(pod.id).conversations.list({ pod_id: pod.id, limit: 15 });
            return (listed.items ?? []).map((row) => {
                const one = row as { metadata?: Record<string, unknown> | null; type?: string; last_activity_at?: string | null; updated_at?: string; created_at?: string };
                return { origin: originOf(one.metadata, one.type), activityAt: one.last_activity_at ?? one.updated_at ?? one.created_at ?? null };
            });
        },
        enabled: listening,
        refetchInterval: listening ? 4000 : false,
    });
    useEffect(() => {
        if (!recent.data || heard) return;
        const found = heardOn(recent.data, on, since.current);
        if (found) {
            setHeard(found);
            captureEvent("onboarding.first_hello", { place: found.toLowerCase() });
        }
    }, [recent.data, heard, on]);
    /* Said on the left for a few seconds; the row keeps saying it. */
    const [saying, setSaying] = useState(false);
    useEffect(() => {
        if (!heard) return;
        setSaying(true);
        const done = setTimeout(() => setSaying(false), 3600);
        return () => clearTimeout(done);
    }, [heard]);
    const told = on.join(" ") + "|" + (saying ? heard ?? "" : "");
    useEffect(() => {
        onChange({ on, heard: saying ? heard : null });
    }, [told]);

    const [turning, setTurning] = useState<PlaceKey | null>(null);
    const [problem, setProblem] = useState<string | null>(null);
    const turnOn = useMutation({
        mutationFn: (key: PlaceKey) => source.connectSystem(pod.id, key),
        onMutate: (key) => { setTurning(key); setProblem(null); },
        onSuccess: (_surface, key) => {
            captureEvent("onboarding.place_on", { place: key.toLowerCase() });
            void queryClient.invalidateQueries({ queryKey: ["arrival-surfaces", pod.id] });
        },
        onError: (failure, key) => setProblem(`${NAMES[key]} didn’t turn on. ${failure instanceof Error ? failure.message : ""}`.trim()),
        onSettled: () => setTurning(null),
    });
    const addSlack = useMutation({
        mutationFn: () => source.startSlackInstall(orgId),
        onSuccess: (url) => {
            window.open(url, "_blank", "noopener");
            setSlackWaiting(true);
        },
        onError: (failure) => setProblem(`Slack didn’t open. ${failure instanceof Error ? failure.message : ""}`.trim()),
    });
    useEffect(() => { if (on.includes("SLACK")) setSlackWaiting(false); }, [on]);

    const me = useQuery({ queryKey: ["current-user"], queryFn: () => lemma().users.current(), staleTime: 5 * 60_000 });
    const phone = ((me.data as { mobile_number?: string | null } | undefined)?.mobile_number ?? "").replace(/[^\d]/g, "");

    const who = firstName.trim() ? `, ${firstName.trim()}` : "";

    return (
        <>
            <InstallStrip plugins={plugins} run={run} />
            <h1 className="ar__t">Write to me from anywhere{who}.</h1>
            <p className="ar__model">
                {places.some((one) => one.state !== "missing") || reading
                    ? "You don’t have to open Lemma to talk to me. Turn on the places you’d like to reach me, and I’ll answer right where you write."
                    : "Once these are set up on this server, you can talk to me from any of them and I’ll answer right where you write. Until then, talk to me inside Lemma."}
            </p>
            {reading ? (
                <p className="ar__note" role="status">Finding where you can reach {pod.name}…</p>
            ) : (
                <ul className="arp" aria-label={`Where to reach ${pod.name}`}>
                    {places.map((place) => (
                        <PlaceRow
                            key={place.key}
                            place={place}
                            pod={pod}
                            heard={heard === place.key}
                            listening={listening}
                            turning={turning === place.key}
                            slackWaiting={slackWaiting}
                            phone={phone}
                            onTurnOn={() => turnOn.mutate(place.key)}
                            onSlack={() => addSlack.mutate()}
                        />
                    ))}
                </ul>
            )}
            {problem && <p className="ar__problem" role="alert">{problem}</p>}
            <div className="ar__acts">
                <span className="ar__sum">{on.length > 0 ? `On: ${listed(on.map((key) => NAMES[key]))}` : "You can turn these on later too."}</span>
                <button type="button" className="ar__quiet" onClick={onDone}>Later</button>
                <button type="button" className="ar__btn ar__btn--primary" onClick={onDone}>{run.done || plugins.length === 0 ? "See what’s ready" : "Continue"}</button>
            </div>
        </>
    );
}

function PlaceRow({ place, pod, heard, listening, turning, slackWaiting, phone, onTurnOn, onSlack }: {
    place: Place;
    pod: Pod;
    heard: boolean;
    listening: boolean;
    turning: boolean;
    slackWaiting: boolean;
    phone: string;
    onTurnOn: () => void;
    onSlack: () => void;
}) {
    const name = NAMES[place.key];
    const link = helloLink(place.key, place.handle, pod.name);
    const state = heard ? "heard"
        : place.state === "on" ? "on"
            : place.state === "missing" ? "missing"
                : turning ? "turning"
                    : place.key === "SLACK" && slackWaiting ? "waiting"
                        : "off";
    /* Not here: said in the row, plainly, with what can be done about it. */
    const line = state === "heard"
        ? <><span className="arp__on"><i />Heard you</span> · just now</>
        : state === "on"
            ? <><span className="arp__on"><i />On</span>{place.key === "EMAIL" ? " · ready now, nothing to turn on" : place.key === "SLACK" ? ` · message @${pod.name}` : " · say hi to start"}</>
            : state === "missing"
                ? <span className="arp__missing"><WarningIcon size={13} weight="regular" aria-hidden="true" />{place.why === "later"
                    ? `Not one tap on this server. You can connect ${name} yourself from ${pod.name} later.`
                    : "Not set up on this server yet. Whoever runs your Lemma can switch it on."}</span>
            : state === "waiting"
                ? "Finish in the Slack tab. I’ll notice when you’re done."
                : state === "turning"
                    ? "Turning on…"
                    : place.key === "SLACK"
                        ? "For the Slack you work in. Message me there, or @mention me where your team talks."
                        : "One tap. Then message me like any contact.";
    const action = state === "off"
        ? place.key === "SLACK"
            ? <button type="button" className="ar__btn ar__btn--small" onClick={onSlack}>Add to Slack <ExternalIcon size={13} /></button>
            : <button type="button" className="ar__btn ar__btn--small" onClick={onTurnOn}>Turn on</button>
        : state === "turning" || state === "waiting"
            ? <span className="arp__spin" aria-hidden="true" />
            : null;
    const knows = place.key === "WHATSAPP"
        ? (phone ? `I’ll know it’s you by the number on your profile, ending ${phone.slice(-4)}.` : null)
        : place.key === "TELEGRAM"
            ? "Telegram asks once to share your contact. That’s how I know it’s you."
            : place.key === "EMAIL"
                ? "Write from the address you signed up with and I’ll answer. Forward me anything. Mail from anyone else waits for you to see."
                : null;
    return (
        <li className="arp__place" data-state={state}>
            <div className="arp__row">
                <span className="arp__logo" data-place={place.key}><PlaceLogo place={place.key} size={22} /></span>
                <span className="arp__said">
                    <span className="arp__name">{name}</span>
                    <span className="arp__line">{line}</span>
                </span>
                {action}
            </div>
            {state === "heard" && (
                <div className="arp__body">
                    <span className="ar__note">Message me on {name} any time; I answer there.</span>
                </div>
            )}
            {state === "on" && place.handle && (
                <div className="arp__body" data-qr={link ? "" : undefined}>
                    <div className="arp__left">
                        <Handle text={place.handle} />
                        {link && (
                            <span className="arp__acts">
                                <a className="ar__btn ar__btn--primary arp__hello" data-place={place.key} href={link} target="_blank" rel="noreferrer">Say hi on {name}</a>
                                {listening && <span className="arp__waiting" data-place={place.key}><i aria-hidden="true" />Waiting for your hello</span>}
                            </span>
                        )}
                        {knows && <span className="ar__note arp__knows">{knows}</span>}
                        {place.key === "WHATSAPP" && !phone && <KnownSender />}
                    </div>
                    {link && (
                        <span className="arp__qr">
                            <span className="verify__qr-paper"><QRCode value={link} size={104} bgColor="#ffffff" fgColor="#111111" /></span>
                            <span>Or scan with your phone</span>
                        </span>
                    )}
                </div>
            )}
        </li>
    );
}

function Handle({ text }: { text: string }) {
    const [copied, setCopied] = useState(false);
    const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
    useEffect(() => () => clearTimeout(timer.current), []);
    return (
        <span className="arp__handle">
            <span>{text}</span>
            <button
                type="button"
                className="arp__copy"
                aria-label={`Copy ${text}`}
                onClick={() => {
                    clearTimeout(timer.current);
                    copyText(text).then(() => { setCopied(true); timer.current = setTimeout(() => setCopied(false), 1600); }).catch(() => undefined);
                }}
            >
                {copied ? <CheckIcon size={14} /> : <CopyIcon size={14} />}
            </button>
        </span>
    );
}
