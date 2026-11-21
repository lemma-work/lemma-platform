"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { source, type ConversationRef, type Pod } from "@/data";
import { ArrivalStage, type StageActions, type StageModel } from "@/arrival/stage";
import { placeKeyOf, type PlaceKey } from "@/arrival/reach";
import type { StageSlot } from "@/arrival/stage-model";
import { readableName } from "@/library/reading";
import { useSurfaces } from "@/shell/surfaces";
import { TeammateFace } from "@/space/teammate-face";
import { useAboutLines } from "@/space/about-lines";
import { AppCover } from "@/stage/app-cover";
import type { FrameActions } from "./actions";
import { NeedRow } from "./answer";
import { AskBox, useApps } from "./ask";
import { appName } from "./lemma-apps";
import { AppMark, AppPoster, sceneSlug } from "./app-mark";
import { useGoingIn, type GoingIn } from "./installs";
import { LemmaActions } from "./lemma-bar";
import { PageActsSlot } from "./page-acts";
import { useLemmaPages, useLemmaTables } from "./lemma-head";
import { bringInRequest, isRunning, lemmaSays, outcomeView, type Ask, type Go, type OutcomeTab, type QueueItem, type View } from "./model";
import { Avatar, Icon, to, type IconName } from "./parts";
import { Problem } from "./calm";
import { SHOW_AFTER_MS, Sk, useLasted } from "@/ui/skeleton";
import { greeting } from "./today";

/** How many apps stand around its face; the rest are in the grid below. */
const SPOTS = 4;
/** Needs-you rows beside the box before "All". */
const NEEDS_SHOWN = 3;
/** Faces in the bar before a count. */
const FACES_SHOWN = 3;
/** Where a team's work usually is today. The Lemma does the moving (its
 *  bring-in skill); this only names the places, so an empty Lemma has a
 *  start that needs no typing. */
const BRING_FROM = ["Notion", "Google Drive", "Airtable", "Google Sheets", "Slack"] as const;

/** One Lemma's page, as the way in drew it: the Lemma itself on the left,
 *  alive, with its apps around its face and the places it answers docked
 *  under it; on the right what it says right now, the box to ask it (files
 *  dropped on it go with the words) and what waits on the reader. Under
 *  both, everything it holds: its apps, then a section for each thing it
 *  keeps or does, each opening a page of its own. No tabs: the sections are
 *  the way in. */
export function LemmaPage({ pod, activity, queue, me, go, onAsk, actions }: {
    pod: Pod;
    activity: readonly ConversationRef[];
    queue: readonly QueueItem[];
    /** The reader's first name, for its greeting. */
    me: string;
    go: Go;
    onAsk: Ask;
    actions: FrameActions;
}) {
    const solo = useMemo(() => [pod], [pod]);
    const { apps, pending: appsPending, failed: appsFailed, retry: retryApps, retrying: retryingApps } = useApps(solo);
    /* Their shapes only once the read has taken a moment. */
    const appsShaped = useLasted(appsPending, SHOW_AFTER_MS);
    const surfaces = useSurfaces(pod.id);
    const running = activity.filter(isRunning).length;
    const [now] = useState(() => Date.now());
    /* A read that failed is not an empty Lemma. */
    const empty = !appsPending && appsFailed.length === 0 && apps.length === 0 && activity.length === 0;

    /* It waves once when the reader arrives, and looks up when they type. */
    const [greeted, setGreeted] = useState(0);
    useEffect(() => {
        const wave = window.setTimeout(() => setGreeted(1), 500);
        return () => window.clearTimeout(wave);
    }, [pod.id]);

    const reach = useMemo<PlaceKey[]>(() => {
        const keys = (surfaces.data ?? []).filter((one) => one.active !== false).map((one) => placeKeyOf(one.platform));
        return [...new Set(keys.filter((key): key is PlaceKey => key !== null))];
    }, [surfaces.data]);
    /* Its apps stand around its face; one going in stands there too, its
       ring filling, and becomes one of them when it lands. Each spot opens
       one thing, and always the same one: an app that is in opens; one still
       going in, or stopped, opens the Add page, where its progress is; the
       empty spot adds one. An install that has landed is drawn as its app,
       never as a second spot beside it -- matched by its app's name, its
       plugin's slug, or its name, since the install may not say which app
       it made. */
    const going = useGoingIn(pod.id);
    const { slots, targets } = useMemo(() => {
        const targets = new Map<string, View>();
        const addPage = outcomeView(pod.id, "add");
        const names = new Set(apps.map(({ app }) => appName(app).toLowerCase()));
        const labels = new Set(apps.map(({ app }) => app.label.toLowerCase()));
        const landed = (one: GoingIn) => [one.app, one.slug].some((name) => Boolean(name) && names.has((name as string).toLowerCase())) || labels.has(one.name.toLowerCase());
        const arriving = going
            .filter((one) => !(one.kind === "added" && landed(one)))
            .map((one): StageSlot => {
                const key = "going:" + one.slug;
                targets.set(key, one.kind === "added" && one.app ? { name: "app", podId: pod.id, app: one.app } : addPage);
                return { key, slug: one.slug, label: one.name, state: one.kind === "added" ? "in" : one.kind === "adding" ? "going" : one.kind === "waiting" ? "next" : "stopped", progress: one.progress };
            });
        const standing = apps.map(({ app }): StageSlot => {
            targets.set(app.id, { name: "app", podId: pod.id, app: appName(app) });
            return { key: app.id, slug: sceneSlug(appName(app), app.label), label: app.label, state: "in", progress: 1 };
        });
        const shown = [...arriving, ...standing].slice(0, SPOTS);
        /* While a spot is free, one empty spot that adds an app: the way to
           more is where the apps already are. Not while its apps read. */
        targets.set("add", addPage);
        const slots: StageSlot[] = shown.length < SPOTS && !appsPending ? [...shown, { key: "add", slug: null, label: "Add an app", state: "empty", progress: 0 }] : shown;
        return { slots, targets };
    }, [apps, going, appsPending, pod.id]);
    const model: StageModel = {
        pod: { id: pod.id, name: pod.name, iconUrl: pod.iconUrl ?? null },
        slots,
        you: me.trim().charAt(0).toUpperCase() || null,
        reach,
        mood: running > 0 ? "thinking" : queue.length > 0 ? "waiting" : "idle",
        greeting: greeted,
        typing: 0,
        heard: null,
        burst: 0,
        part: null,
    };
    const status = running > 0 ? `Working on ${running} ${running === 1 ? "thing" : "things"}` : queue.length > 0 ? "Waiting on you" : "Ready";
    const says = `${greeting(now)}${me ? ", " + me : ""}. ${lemmaSays({ empty, running, needs: queue.length })}`;
    const people = pod.members.filter((member) => member.kind === "person");
    /* What pressing each part around the face does. */
    const act: StageActions = {
        name: pod.name,
        slot: (slot) => go(targets.get(slot.key) ?? outcomeView(pod.id, "add")),
        you: () => go(outcomeView(pod.id, "people")),
        others: Math.max(0, people.length - 1),
        place: () => actions.connect(pod.id),
    };

    return (
        <div className="ox-lp">
            <div className="ox-lp__bar">
                {people.length > 0 && (
                    <a className="ox-lhead__faces" {...to(outcomeView(pod.id, "people"), go)} aria-label={`People in ${pod.name}: ${people.map((member) => member.name).join(", ")}`}>
                        {people.slice(0, FACES_SHOWN).map((member) => <Avatar key={member.id} name={member.name} size={26} />)}
                        {people.length > FACES_SHOWN && <span className="ox-lhead__more ox-num">+{people.length - FACES_SHOWN}</span>}
                    </a>
                )}
                <button type="button" className="ox-btn ox-btn--small" onClick={() => actions.addPeople(pod.id)}>Add people</button>
                <LemmaActions pod={pod} go={go} actions={actions} newChat={false} />
            </div>
            <div className="ox-lp__top">
                <section className="ox-lp__scene" aria-label={pod.name}>
                    <div className="ox-scene" data-hero={`face:${pod.id}`}><ArrivalStage model={model} act={act} /></div>
                    <div className="ox-lp__id">
                        <h1 className="ox-lp__name">{pod.name}</h1>
                        <span className="ox-lp__status" data-tone={running > 0 ? "live" : queue.length > 0 ? "wait" : "ok"}><i />{status}</span>
                    </div>
                </section>
                <section className="ox-lp__talk" aria-label={`Talk to ${pod.name}`}>
                    <p className="ox-lp__says">{says}</p>
                    <AskBox pods={solo} podId={pod.id} onAsk={onAsk} placeholder={`Ask ${pod.name} anything, or drop files here`} />
                    {empty && (
                        <div className="ox-lp__bring">
                            <p className="ox-lp__label">Or bring your work in from</p>
                            <div className="ox-lp__chips">
                                {BRING_FROM.map((place) => (
                                    <button key={place} type="button" className="ox-lp__chip" onClick={() => actions.prompt(pod.id, bringInRequest([place]))}>{place}</button>
                                ))}
                            </div>
                        </div>
                    )}
                    {queue.length > 0 && (
                        <div className="ox-lp__needs">
                            <p className="ox-lp__label">
                                Needs you <span className="ox-num">{queue.length}</span>
                                {queue.length > NEEDS_SHOWN && <a className="ox-link" {...to({ name: "needs" }, go)}>All</a>}
                            </p>
                            <div className="ox-queue">
                                {queue.slice(0, NEEDS_SHOWN).map((item) => <NeedRow key={item.key} item={item} pods={solo} now={now} go={go} showLemma={false} />)}
                            </div>
                        </div>
                    )}
                </section>
            </div>
            <div className="ox-lp__holds">
                <section className="ox-hold ox-hold--apps" aria-label="Apps">
                    <header className="ox-hold__head">
                        <h2><a {...to(outcomeView(pod.id, "apps"), go)} data-hero="title:apps">Apps</a></h2>
                        {apps.length > 0 && <span className="ox-num">{apps.length}</span>}
                    </header>
                    {appsFailed.length > 0 && (
                        <div className="ox-sectfail">
                            <Problem retrying={retryingApps} onRetry={retryApps}>Couldn’t read {pod.name}’s apps.</Problem>
                        </div>
                    )}
                    <div className="ox-lp__apps" aria-busy={appsPending || undefined}>
                        {appsShaped && [0, 1].map((index) => (
                            <span key={"shape" + index} className="ox-lp__app" aria-hidden="true">
                                <Sk r={12} style={{ aspectRatio: "16 / 10", height: "auto" }} />
                                <Sk w={index === 0 ? "64%" : "48%"} />
                            </span>
                        ))}
                        {apps.map(({ app }) => (
                            <a key={app.id} className="ox-lp__app" {...to({ name: "app", podId: pod.id, app: appName(app) }, go)}>
                                <span className="ox-lp__cover"><AppCover url={app.url} fallback={<AppPoster name={appName(app)} label={app.label} />} /></span>
                                <span className="ox-lp__appname"><AppMark name={appName(app)} label={app.label} size={22} /><span className="ox-lp__appt">{app.label}</span></span>
                            </a>
                        ))}
                        <a className="ox-lp__app ox-lp__app--add" {...to(outcomeView(pod.id, "add"), go)}>
                            <span className="ox-lp__cover"><Icon name="plus" size={26} /></span>
                            <span className="ox-lp__appname">Add an app</span>
                        </a>
                    </div>
                </section>
                <LemmaParts pod={pod} go={go} actions={actions} />
            </div>
        </div>
    );
}

/* ── what it holds, how it works ──────────────────────────────────────── */

interface Part {
    key: string;
    title: string;
    icon: IconName;
    /** One line of what is in it now; null while it is read. */
    line: string | null;
    /** Something in it waits on a person. */
    needs?: boolean;
    /** What the card says while its line is read, or when it can't be. */
    blurb: string;
    view?: View;
    onOpen?: () => void;
    /** The section's page this card's title grows into, going in. */
    tab?: OutcomeTab;
}

/** Names, two of them and how many more: "Deals, People +3". */
function firstFew(names: readonly string[], shown = 2): string {
    const head = names.slice(0, shown).join(", ");
    return names.length > shown ? head + " +" + (names.length - shown) : head;
}

/** Root names that have a section of their own, or are its own. */
const NOT_FILES = new Set(["memory", "skills", "pages", "me", "system", "plan.md"]);

/** Everything a Lemma holds and how it works, as cards in two groups: each
 *  thing's mark, its name, one line of what is in it now, and the way in.
 *  The lines are the ones its Settings page shows (`useAboutLines`), so
 *  the two never disagree; files, tables and pages are counted here. A warm
 *  dot is only ever beside a line that says what is wrong. */
function LemmaParts({ pod, go, actions }: { pod: Pod; go: Go; actions: FrameActions }) {
    const lines = useAboutLines(pod);
    const files = useQuery({ queryKey: ["outcome-files", pod.id], queryFn: () => source.listLibrary(pod.id, "files", "/"), staleTime: 5 * 60_000, retry: false });
    const tables = useLemmaTables(pod.id);
    const pages = useLemmaPages(pod.id);
    const mine = (files.data?.items ?? []).filter((one) => !one.name.startsWith(".") && !NOT_FILES.has(one.name));
    const folders = mine.filter((one) => one.kind === "folder").length;
    const loose = mine.length - folders;
    const filesLine = files.isPending ? null : files.isError ? null
        : mine.length === 0 ? "Nothing yet"
        : [loose ? `${loose} ${loose === 1 ? "file" : "files"}` : "", folders ? `${folders} ${folders === 1 ? "folder" : "folders"}` : ""].filter(Boolean).join(", ");
    const settings = (section: string) => outcomeView(pod.id, "settings", section);
    const line = (key: keyof typeof lines) => lines[key]?.text ?? null;

    const holds: Part[] = [
        { key: "files", title: "Files", icon: "file", line: filesLine, blurb: "Everything handed to it", view: outcomeView(pod.id, "files"), tab: "files" },
        { key: "tables", title: "Tables", icon: "table", line: tables.isPending ? null : tables.isError ? null : tables.data?.length ? firstFew(tables.data.map((one) => readableName(one.name))) : "Nothing yet", blurb: "What it keeps track of", view: outcomeView(pod.id, "tables"), tab: "tables" },
        { key: "pages", title: "Pages", icon: "page", line: pages.isPending ? null : pages.isError ? null : pages.data?.length ? firstFew(pages.data.map((one) => readableName(one.name.replace(/\.md$/i, "")))) : "Nothing yet", blurb: "What it has written up", view: outcomeView(pod.id, "pages"), tab: "pages" },
        { key: "memory", title: "Memory", icon: "brain", line: line("memory"), needs: lines.memory?.needs, blurb: "Notes it wrote itself", view: outcomeView(pod.id, "memory"), tab: "memory" },
        { key: "skills", title: "Skills", icon: "skill", line: line("skills"), needs: lines.skills?.needs, blurb: "How you do things, written down", view: outcomeView(pod.id, "skills"), tab: "skills" },
    ];
    const works: Part[] = [
        { key: "automations", title: "Automations", icon: "clock", line: line("schedules"), needs: lines.schedules?.needs, blurb: "What it does on its own", view: outcomeView(pod.id, "automations"), tab: "automations" },
        { key: "agents", title: "Agents", icon: "agent", line: line("agents"), needs: lines.agents?.needs, blurb: "Helpers it hands work to", view: outcomeView(pod.id, "agents"), tab: "agents" },
        { key: "connections", title: "Connections", icon: "plug", line: line("connections"), needs: lines.connections?.needs, blurb: "What it works through", view: settings("connections") },
        { key: "reach", title: "Where to reach it", icon: "cast", line: line("channels"), needs: lines.channels?.needs, blurb: "WhatsApp, Slack, email and more", onOpen: () => actions.connect(pod.id) },
        { key: "people", title: "People", icon: "people", line: line("people"), needs: lines.people?.needs, blurb: "Who can open it", view: outcomeView(pod.id, "people"), tab: "people" },
        { key: "model", title: "Default model", icon: "chip", line: line("model"), needs: lines.model?.needs, blurb: "What it thinks with", view: settings("model") },
        { key: "mcp", title: "MCP", icon: "out", line: line("ai-tools"), needs: lines["ai-tools"]?.needs, blurb: "ChatGPT, Claude and others", view: settings("ai-tools") },
        { key: "others", title: "Other Lemmas", icon: "link", line: line("works-with"), needs: lines["works-with"]?.needs, blurb: "Other Lemmas it can ask", view: settings("works-with") },
    ];
    return (
        <>
            <PartGroup title={`What ${pod.name} holds`} parts={holds} go={go} />
            <PartGroup title={`How ${pod.name} works`} parts={works} go={go} />
        </>
    );
}

function PartGroup({ title, parts, go }: { title: string; parts: readonly Part[]; go: Go }) {
    return (
        <section className="ox-parts" aria-label={title}>
            <h2 className="ox-parts__h">{title}</h2>
            <div className="ox-parts__grid">
                {parts.map((part) => {
                    const inner = (
                        <>
                            <span className="ox-part__i"><Icon name={part.icon} size={21} /></span>
                            <span className="ox-part__w">
                                <span className="ox-part__t" data-hero={part.tab ? `title:${part.tab}` : undefined}>{part.title}</span>
                                <span className="ox-part__s">
                                    {part.needs && part.line && <i className="ox-part__dot" role="img" aria-label="Needs you" />}
                                    {part.line ?? part.blurb}
                                </span>
                            </span>
                            <Icon name="next" size={17} />
                        </>
                    );
                    return part.view
                        ? <a key={part.key} className="ox-part" {...to(part.view, go)}>{inner}</a>
                        : <button key={part.key} type="button" className="ox-part" onClick={part.onOpen}>{inner}</button>;
                })}
            </div>
        </section>
    );
}

/** The top of each of a Lemma's own pages, as every page's header is (kit
 *  `.ph`): the way back to the Lemma, with its face, above the page's title,
 *  one line from the Lemma about what is here, and the page's buttons at the
 *  right. The title wears the same `data-hero` as the section heading on the
 *  Lemma's page, so going in, one grows into the other. */
export function LemmaPageHead({ pod, tab, title, says, go, actions, children }: {
    pod: Pod;
    tab: OutcomeTab;
    title: string;
    says?: string | null;
    go: Go;
    actions: FrameActions;
    /** The page's own buttons, at the right. */
    children?: ReactNode;
}) {
    return (
        <header className="ox-subhead">
            <div className="ph">
                <a className="ph__back" {...to(outcomeView(pod.id), go)} aria-label={`Back to ${pod.name}`}>
                    <Icon name="back" size={15} />
                    <TeammateFace pod={pod} size={20} />
                    {pod.name}
                </a>
                <div className="ph__row">
                    <div className="ph__words">
                        <h1 className="ph__t"><span data-hero={`title:${tab}`}>{title}</span></h1>
                        {says && <p className="ph__line">{says}</p>}
                    </div>
                    <div className="ph__acts">
                        {children}
                        <PageActsSlot />
                        <LemmaActions pod={pod} go={go} actions={actions} newChat={false} />
                    </div>
                </div>
            </div>
        </header>
    );
}
