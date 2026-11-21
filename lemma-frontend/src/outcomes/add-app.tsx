"use client";

import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from "react";
import { source, type Pod } from "@/data";
import { needsLine } from "@/arrival/apps-step";
import { GlyphIcon } from "@/arrival/glyphs";
import { sourceOfInstall } from "@/arrival/install-plan";
import { stepWords } from "@/arrival/install-state";
import { addHref, rankPlugins } from "@/arrival/suggest";
import type { JobId } from "@/arrival/work";
import { usePodMay } from "@/connect/pod-connect";
import { BUILT, LINES, PLANNED, type BuiltPlugin, type PlannedPlugin } from "@/site/plugins/catalog";
import { Shot } from "@/site/plugins/shot";
import { TeammateFace } from "@/space/teammate-face";
import type { FrameActions } from "./actions";
import { takePointed } from "./add-point";
import { useApps, type PodApp } from "./ask";
import { useAddPlugin, type AddRow } from "./installs";
import { outcomeView, type Go } from "./model";
import { Icon, to } from "./parts";

/** Example apps, each finishing "An app that…": one press fills the box. */
const EXAMPLES: readonly string[] = [
    "tracks renewals due this quarter, with who owns each and what’s next",
    "takes demo requests and books a call with whoever owns the deal",
    "sends me a summary of open deals every Friday",
    "lists open customer questions by how long they’ve waited",
];

/** How many of the plugins still being made show before "Show all". */
const SOON_SHOWN = 6;
/** How many of a Lemma's apps sit beside "Adding to" before a count. */
const DOTS_SHOWN = 6;

/** A plugin's app is named for its folder, except where the folder names
 *  the job and the app its screen. The catalog carries no app names, so
 *  the one exception is written here; an app this misses only shows Add
 *  rather than "In", and adding again changes nothing that is there. */
const APP_OF: Record<string, readonly string[]> = { mail: ["inbox"] };

/** The app a plugin put in this Lemma, if one is there: by its slug, its
 *  folder, or the name above. */
function appIn(plugin: Pick<BuiltPlugin, "slug" | "folder">, names: ReadonlySet<string>): string | null {
    const candidates = [plugin.slug, plugin.folder, ...(APP_OF[plugin.slug] ?? [])];
    return candidates.find((name) => names.has(name)) ?? null;
}

function appNames(apps: readonly PodApp[]): ReadonlySet<string> {
    return new Set(apps.map(({ app }) => app.id.replace(/^app:/, "")));
}

function lowerFirst(text: string): string {
    return text.charAt(0).toLowerCase() + text.slice(1);
}

/** Whether a plugin has a folder the server's install queue can read. */
function installable(plugin: Pick<BuiltPlugin, "install">): boolean {
    return Boolean(plugin.install && sourceOfInstall(plugin.install));
}

/** Adding an app to a Lemma, as a page of its own. Describing your own
 *  comes first, because that is what a Lemma is for: the words start a chat
 *  with it and it builds the app there. Under it, every plugin in the
 *  catalog -- the built ones with their real screens, Add and Customize on
 *  each; the ones still being made marked so, with an offer to build one
 *  like it now. Add queues the install on the server and the card follows
 *  it in a ring around the app's mark; the reader can leave, and the Lemma
 *  tells them in their chat when it is in. The page's header, above, says
 *  what this is, so nothing here repeats it. */
export function AddAppPage({ pod, pods, go, actions }: { pod: Pod; pods: readonly Pod[]; go: Go; actions: FrameActions }) {
    const solo = useMemo(() => [pod], [pod]);
    const { apps, pending } = useApps(solo);
    const names = useMemo(() => appNames(apps), [apps]);
    const adding = useAddPlugin(pod.id);
    const may = usePodMay(pod.id, "pod.update");
    /* Asked before the answer is in: the server refuses in the card's own
       row if it must. */
    const editor = may.allowed || !may.answered;
    const live = source.label === "live";

    const [text, setText] = useState("");
    const box = useRef<HTMLTextAreaElement>(null);
    const [query, setQuery] = useState("");
    const [shelf, setShelf] = useState<string | null>(null);
    const [allSoon, setAllSoon] = useState(false);

    const words = query.trim().toLowerCase();
    const shows = (plugin: { line: string }, haystack: string) => (shelf === null || plugin.line === shelf) && (!words || haystack.toLowerCase().includes(words));
    const built = BUILT.filter((plugin) => shows(plugin, `${plugin.name} ${plugin.job} ${plugin.line}`));
    const soon = PLANNED.filter((plugin) => shows(plugin, `${plugin.name} ${plugin.one} ${plugin.line}`));
    const narrowed = Boolean(words) || shelf !== null;
    const soonShown = allSoon || narrowed ? soon : soon.slice(0, SOON_SHOWN);
    const shelves = LINES.filter((one) => BUILT.some((plugin) => plugin.line === one.id) || PLANNED.some((plugin) => plugin.line === one.id));

    /* Sent here from a ready-made app elsewhere: its card, in view and lit
       once (add-point.ts). */
    useEffect(() => {
        const slug = takePointed();
        if (!slug) return;
        const frame = window.requestAnimationFrame(() => {
            const card = document.getElementById("plugin-" + slug);
            if (!card) return;
            card.scrollIntoView({ block: "center" });
            card.setAttribute("data-pointed", "");
            window.setTimeout(() => card.removeAttribute("data-pointed"), 1800);
        });
        return () => window.cancelAnimationFrame(frame);
    }, []);

    /* Words from elsewhere on the page into the box, ready to send. */
    const describe = (said: string) => {
        setText(said);
        box.current?.scrollIntoView({ block: "center", behavior: "smooth" });
        box.current?.focus({ preventScroll: true });
    };

    /* Why Add is not on a card, when it is not: said once, over them all. */
    const why = !live
        ? "This is a sample, so nothing goes in from here. Each app’s page shows it working."
        : !editor
            ? `Adding apps to ${pod.name} is for its editors, so ask one of them. You can still describe what you need, above.`
            : null;

    return (
        <div className="ox-add">
            <AddingTo pod={pod} pods={pods} apps={apps} pending={pending} rows={adding.rows} go={go} />

            <CustomBox pod={pod} text={text} setText={setText} box={box} onSend={(said) => actions.ask(pod.id, "Build an app that " + said)} />

            <section className="ox-add__shelf" aria-labelledby="ox-add-shelf">
                <header className="ox-add__shelfhead">
                    <span className="ox-add__said">
                        <h2 id="ox-add-shelf">Or start from a plugin</h2>
                        <span className="ox-add__small">Ready-made apps. Add one as it is, or customize it first.</span>
                    </span>
                    <label className="ox-add__find">
                        <Icon name="search" size={15} />
                        <span className="ox-sr">Search plugins</span>
                        <input type="search" value={query} placeholder="Search" onChange={(event) => setQuery(event.target.value)} />
                    </label>
                </header>
                {shelves.length > 1 && (
                    <div className="ox-add__pills" role="group" aria-label="Kinds of work">
                        <button type="button" aria-pressed={shelf === null} onClick={() => setShelf(null)}>All</button>
                        {shelves.map((one) => (
                            <button key={one.id} type="button" aria-pressed={shelf === one.id} onClick={() => setShelf((was) => (was === one.id ? null : one.id))}>{one.name}</button>
                        ))}
                    </div>
                )}
                {why && <p className="ox-add__why">{why}</p>}

                {built.length === 0 && soon.length === 0 ? (
                    <div className="ox-add__none">
                        <p>Nothing here matches{words ? ` “${query.trim()}”` : ""}.</p>
                        <button type="button" className="ox-btn ox-add__btn" onClick={() => describe(query.trim())}>Describe it instead, and I’ll build it</button>
                    </div>
                ) : (
                    <>
                        {built.length > 0 && (
                            <ul className="ox-add__grid">
                                {built.map((plugin) => (
                                    <PluginCard
                                        key={plugin.slug}
                                        plugin={plugin}
                                        pod={pod}
                                        row={adding.rows.get(plugin.slug) ?? null}
                                        inApp={appIn(plugin, names)}
                                        checking={pending}
                                        mayAdd={live && editor}
                                        onAdd={() => adding.add(plugin)}
                                        onRetry={() => adding.retry(plugin.slug)}
                                        go={go}
                                        actions={actions}
                                    />
                                ))}
                            </ul>
                        )}
                        {soon.length > 0 && (
                            <div className="ox-add__soonwrap">
                                <h3 className="ox-add__soonhead">Coming soon <span className="ox-num">{soon.length}</span></h3>
                                <ul className="ox-add__soon">
                                    {soonShown.map((plugin) => <SoonCard key={plugin.slug} plugin={plugin} pod={pod} actions={actions} />)}
                                </ul>
                                {soonShown.length < soon.length && (
                                    <button type="button" className="ox-link ox-add__more" onClick={() => setAllSoon(true)}>Show {soon.length - soonShown.length} more</button>
                                )}
                            </div>
                        )}
                    </>
                )}
            </section>
        </div>
    );
}

/** Which Lemma the apps go into, and what it already has: a picker when the
 *  reader has more than one, and its apps as marks, the ones on their way
 *  in with their rings filling -- so a press on a card shows here too. */
function AddingTo({ pod, pods, apps, pending, rows, go }: {
    pod: Pod;
    pods: readonly Pod[];
    apps: readonly PodApp[];
    pending: boolean;
    rows: ReadonlyMap<string, AddRow>;
    go: Go;
}) {
    const names = appNames(apps);
    const going = [...rows.values()].filter((row) => row.kind === "waiting" || row.kind === "adding" || (row.kind === "added" && !(row.app && names.has(row.app))));
    const marks = [
        ...going.map((row) => ({ key: "going:" + row.slug, slug: row.slug, label: row.name, state: row.kind === "added" ? "in" : row.kind, progress: row.progress, app: row.app })),
        ...apps.map(({ app }) => ({ key: app.id, slug: app.id.replace(/^app:/, ""), label: app.label, state: "in", progress: 1, app: app.id.replace(/^app:/, "") })),
    ];
    const shown = marks.slice(0, DOTS_SHOWN);
    return (
        <div className="ox-add__to">
            <span className="ox-add__tolabel">Adding to</span>
            {pods.length > 1 ? (
                <label className="ox-add__pick">
                    <TeammateFace pod={pod} size={22} />
                    <span className="ox-sr">The Lemma to add to</span>
                    <select value={pod.id} onChange={(event) => go(outcomeView(event.target.value, "add"))}>
                        {pods.map((one) => <option key={one.id} value={one.id}>{one.name}</option>)}
                    </select>
                    <Icon name="chevron" size={12} />
                </label>
            ) : (
                <span className="ox-add__pick ox-add__pick--one"><TeammateFace pod={pod} size={22} />{pod.name}</span>
            )}
            <span className="ox-add__in" aria-live="polite">
                {pending && marks.length === 0 ? (
                    <span className="ox-add__small">Checking what’s in {pod.name}…</span>
                ) : marks.length === 0 ? (
                    <span className="ox-add__small">No apps in it yet</span>
                ) : (
                    <>
                        <span className="ox-add__small ox-add__inlabel">In {pod.name}</span>
                        {shown.map((mark) => {
                            const ring = <Mark slug={mark.slug} state={mark.state as MarkState} progress={mark.progress} size={30} />;
                            return mark.app && mark.state === "in"
                                ? <a key={mark.key} className="ox-add__dot" title={mark.label} aria-label={`Open ${mark.label}`} {...to({ name: "app", podId: pod.id, app: mark.app }, go)}>{ring}</a>
                                : <span key={mark.key} className="ox-add__dot" title={`${mark.label}, going in`}>{ring}</span>;
                        })}
                        {marks.length > shown.length && (
                            <a className="ox-add__dotmore ox-num" {...to(outcomeView(pod.id, "apps"), go)} aria-label={`All ${marks.length} apps in ${pod.name}`}>+{marks.length - shown.length}</a>
                        )}
                    </>
                )}
            </span>
        </div>
    );
}

/** Describing your own: the sentence the reader finishes is the one the
 *  Lemma is sent, so what they see is what it reads. Examples fill the box
 *  for them to change; sending starts a chat where it builds the app. */
function CustomBox({ pod, text, setText, box, onSend }: {
    pod: Pod;
    text: string;
    setText: (text: string) => void;
    box: RefObject<HTMLTextAreaElement | null>;
    onSend: (said: string) => void;
}) {
    const said = text.trim();
    /* The box already says "An app that"; somebody who typed it too is not
       asking for an app that builds an app. */
    const asked = said.replace(/^(?:build\s+)?(?:me\s+)?(?:an?\s+)?app\s+that\s+/i, "").trim();
    const send = () => {
        if (!asked) return;
        onSend(asked);
        setText("");
    };
    return (
        <form className="ox-add__custom" aria-labelledby="ox-add-custom" onSubmit={(event) => { event.preventDefault(); send(); }}>
            <span className="ox-add__tag"><Icon name="plus" size={12} />Custom</span>
            <h2 id="ox-add-custom" className="ox-add__customt">Describe the app you want, and I’ll build it</h2>
            <label className="ox-add__write">
                <span className="ox-add__lead" aria-hidden="true">An app that</span>
                <span className="ox-sr">Describe the app: an app that…</span>
                <textarea
                    ref={box}
                    rows={3}
                    value={text}
                    placeholder="keeps track of …, shows …, sends …"
                    onChange={(event) => setText(event.target.value)}
                    onKeyDown={(event) => {
                        if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                            event.preventDefault();
                            send();
                        }
                    }}
                />
            </label>
            <div className="ox-add__chips" role="group" aria-label="Examples">
                {EXAMPLES.map((example) => (
                    <button key={example} type="button" className="ox-add__chip" aria-pressed={said === example} onClick={() => { setText(example); box.current?.focus(); }}>
                        {example}
                    </button>
                ))}
            </div>
            <div className="ox-add__foot">
                <span className="ox-add__small">This starts a chat with me, {pod.name}. I build it there, ask what I need to, and say when it’s ready.</span>
                <button type="submit" className="ox-btn ox-btn--primary ox-add__btn ox-add__btn--big" disabled={!asked}>Build it</button>
            </div>
        </form>
    );
}

type MarkState = "idle" | "waiting" | "adding" | "in" | "stopped";

/** An app's mark in its circle, with the ring that fills as it goes in
 *  and a tick once it is. */
function Mark({ slug, state, progress, size = 40 }: { slug: string; state: MarkState; progress: number; size?: number }) {
    const share = Math.max(0, Math.min(1, progress));
    return (
        <span className="ox-add__mark" data-state={state} style={{ "--mark": `${size}px` } as CSSProperties} aria-hidden="true">
            <svg className="ox-add__ring" viewBox="0 0 48 48">
                <circle className="ox-add__track" cx="24" cy="24" r="22.5" />
                <circle className="ox-add__done" cx="24" cy="24" r="22.5" pathLength={100} strokeDasharray="100" strokeDashoffset={state === "waiting" ? 78 : 100 - share * 100} />
            </svg>
            <span className="ox-add__glyph"><GlyphIcon slug={slug} size={Math.round(size * 0.46)} /></span>
            <span className="ox-add__tick"><Icon name="check" size={Math.max(8, Math.round(size * 0.24))} /></span>
        </span>
    );
}

function markOf(row: AddRow | null, inApp: string | null): MarkState {
    if (row) {
        if (row.kind === "added") return "in";
        if (row.kind === "failed" || row.kind === "asks") return "stopped";
        return row.kind;
    }
    return inApp ? "in" : "idle";
}

/** One built plugin: its real screen, its mark, what it does and needs, and
 *  what can be done with it here -- Add and Customize; where it is on its
 *  way in; that it is in, with Open; or why it stopped, with Try again. */
function PluginCard({ plugin, pod, row, inApp, checking, mayAdd, onAdd, onRetry, go, actions }: {
    plugin: BuiltPlugin;
    pod: Pod;
    row: AddRow | null;
    /** The app it already put in this Lemma. */
    inApp: string | null;
    /** The Lemma's apps are still being read. */
    checking: boolean;
    mayAdd: boolean;
    onAdd: () => void;
    onRetry: () => Promise<void>;
    go: Go;
    actions: FrameActions;
}) {
    const [retrying, setRetrying] = useState(false);
    const picture = plugin.pictures[0] ?? null;
    const state = markOf(row, inApp);
    const opens = (row?.kind === "added" ? row.app : null) ?? inApp;
    const customize = () => actions.prompt(pod.id, `Add the ${plugin.name} app, changed so that `);
    const change = () => actions.prompt(pod.id, `Change the ${plugin.name} app so that `);

    let acts: ReactNode;
    let said: ReactNode = null;
    if (row && (row.kind === "waiting" || row.kind === "adding")) {
        said = (
            <>
                <span className="ox-add__step">{row.kind === "adding" ? stepWords(row.step) : stepWords(null)}{row.kind === "adding" && <span className="ox-num"> · {Math.round(row.progress * 100)}%</span>}</span>
                <span className="ox-add__small">I’ll tell you in our chat when it’s in.</span>
            </>
        );
    } else if (row && row.kind === "failed") {
        said = <span className="ox-add__stop">{row.reason ?? "It stopped before it was in."} Trying again picks up where it stopped.</span>;
        acts = (
            <button type="button" className="ox-btn ox-add__btn" disabled={retrying} onClick={() => { setRetrying(true); void onRetry().finally(() => setRetrying(false)); }}>
                {retrying ? "Trying again…" : "Try again"}
            </button>
        );
    } else if (row && row.kind === "asks") {
        const href = addHref(plugin);
        said = <span className="ox-add__stop">{row.reason ?? "It needs an answer before it goes in."}</span>;
        acts = href ? <a className="ox-btn ox-add__btn" href={href}>Answer on its page</a> : null;
    } else if (state === "in") {
        said = <span className="ox-add__intag"><Icon name="check" size={12} />{row ? `It’s in ${pod.name}` : `In ${pod.name}`}</span>;
        acts = (
            <>
                {opens && <a className="ox-btn ox-btn--primary ox-add__btn" {...to({ name: "app", podId: pod.id, app: opens }, go)}>Open</a>}
                <button type="button" className="ox-btn ox-add__btn" onClick={change}>Change it</button>
            </>
        );
    } else if (!installable(plugin)) {
        said = <span className="ox-add__small">Not ready to add yet. Customize it and I’ll build one like it.</span>;
        acts = <button type="button" className="ox-btn ox-add__btn" onClick={customize}>Customize</button>;
    } else {
        acts = (
            <>
                {mayAdd
                    ? <button type="button" className="ox-btn ox-btn--primary ox-add__btn" disabled={checking} onClick={onAdd}>{checking ? "Checking…" : "Add"}</button>
                    : <a className="ox-btn ox-add__btn" href={`/templates/${plugin.slug}`} target="_blank" rel="noreferrer">See it</a>}
                <button type="button" className="ox-btn ox-add__btn" onClick={customize}>Customize</button>
            </>
        );
    }

    return (
        <li className="ox-add__card" id={"plugin-" + plugin.slug} data-state={state}>
            <a className="ox-add__pic" href={`/templates/${plugin.slug}`} target="_blank" rel="noreferrer" aria-label={`${plugin.name}: its page`}>
                {picture
                    ? <Shot picture={picture} alt={picture.caption} sizes="(max-width: 760px) 100vw, 340px" />
                    : <span className="ox-add__nopic"><GlyphIcon slug={plugin.slug} size={30} /></span>}
            </a>
            <div className="ox-add__body">
                <div className="ox-add__who">
                    <Mark slug={plugin.slug} state={state} progress={row?.progress ?? (state === "in" ? 1 : 0)} />
                    <span className="ox-add__name">{plugin.name}</span>
                </div>
                <p className="ox-add__job">{plugin.job}</p>
                <p className="ox-add__needs">{needsLine(plugin)}</p>
                <p className="ox-add__said2" role="status">{said}</p>
                {acts && <div className="ox-add__acts">{acts}</div>}
            </div>
        </li>
    );
}

/** A plugin still being made: what it will do, marked as coming, and an
 *  offer to build one like it now -- words in a new chat for the reader to
 *  finish, never an Add that can only fail. */
function SoonCard({ plugin, pod, actions }: { plugin: PlannedPlugin; pod: Pod; actions: FrameActions }) {
    return (
        <li className="ox-add__sooncard">
            <Mark slug={plugin.slug} state="idle" progress={0} size={36} />
            <span className="ox-add__soonbody">
                <span className="ox-add__soonname">{plugin.name}<span className="ox-add__soontag">Coming soon</span></span>
                <span className="ox-add__job">{plugin.one}</span>
                <button type="button" className="ox-link ox-add__build" onClick={() => actions.prompt(pod.id, `Build me a ${plugin.name} app: ${lowerFirst(plugin.one)}. `)}>
                    Build one like it now
                </button>
            </span>
        </li>
    );
}

/* ── apps that fit a Lemma just started ───────────────────────────────── */

/** How many apps a new Lemma is offered. */
const FIT_COUNT = 4;

/** The kinds of work a few everyday words point at, in the server's job
 *  ids, so a Lemma's name and purpose can rank the catalog the way the job
 *  question on the way in does. Matched as word beginnings. */
const JOB_WORDS: Record<JobId, readonly string[]> = {
    SALES: ["sale", "sell", "deal", "lead", "prospect", "client", "customer", "account", "revenue", "crm", "quote"],
    SUPPORT: ["support", "help", "ticket", "question", "service", "complain", "refund"],
    MARKETING: ["market", "brand", "content", "campaign", "social", "launch", "newsletter", "deck", "design"],
    FOUNDER: ["founder", "ceo", "investor", "fundrais", "board", "startup", "company"],
    OPERATIONS: ["operation", "ops", "admin", "office", "process", "invoice", "finance", "vendor"],
    HR: ["hire", "hiring", "recruit", "candidate", "interview", "onboard", "talent"],
    PRODUCT: ["product", "roadmap", "feature", "feedback", "research"],
    PROJECT_MANAGEMENT: ["project", "task", "plan", "deadline", "sprint", "todo"],
    ENGINEERING: ["engineer", "code", "dev", "bug", "deploy", "release", "software"],
};

function tokensOf(text: string): string[] {
    return text.toLowerCase().split(/[^a-z0-9]+/).filter((word) => word.length >= 3);
}

/** The built plugins that fit what a Lemma is called and is for: the job
 *  its words point at, then any word of a plugin's own name or job said
 *  outright. With nothing to go on, the catalog's own order, which puts
 *  the plugins others build on first. */
export function fittingPlugins(text: string, count = FIT_COUNT): BuiltPlugin[] {
    const said = tokensOf(text);
    const jobs = (Object.keys(JOB_WORDS) as JobId[]).filter((job) => JOB_WORDS[job].some((stem) => said.some((word) => word.startsWith(stem))));
    const ranked = rankPlugins(jobs, BUILT);
    const score = (plugin: BuiltPlugin) => {
        const own = tokensOf(`${plugin.name} ${plugin.job}`).filter((word) => word.length >= 4);
        const named = own.filter((word) => said.some((one) => one.length >= 4 && (one.startsWith(word) || word.startsWith(one)))).length;
        return plugin.jobs.filter((job) => jobs.includes(job as JobId)).length * 2 + named;
    };
    const scored = ranked.map((plugin, index) => ({ plugin, index, score: score(plugin) }));
    return scored.sort((a, b) => b.score - a.score || a.index - b.index).slice(0, count).map((one) => one.plugin);
}

/** The last step of starting a Lemma: a few apps that fit it, one press
 *  each, going in through the same queue and the same kept state as the
 *  Add an app page -- so each shows going in on the sidebar and on the
 *  Lemma's scene while the reader carries on. */
export function FittingApps({ podId, name, purpose }: { podId: string; name: string; purpose: string }) {
    const fitting = useMemo(() => fittingPlugins(`${name} ${purpose}`), [name, purpose]);
    const adding = useAddPlugin(podId);
    const live = source.label === "live";
    return (
        <ul className="ox-add__fit" aria-label={`Apps that fit ${name}`}>
            {fitting.map((plugin) => (
                <FitRow key={plugin.slug} plugin={plugin} name={name} live={live} row={adding.rows.get(plugin.slug) ?? null} onAdd={() => adding.add(plugin)} onRetry={() => adding.retry(plugin.slug)} />
            ))}
        </ul>
    );
}

/** One app that fits: its mark, name and job, and Add -- then its ring
 *  and how far along, then that it is in, or why it stopped. */
function FitRow({ plugin, name, live, row, onAdd, onRetry }: {
    plugin: BuiltPlugin;
    name: string;
    live: boolean;
    row: AddRow | null;
    onAdd: () => void;
    onRetry: () => Promise<void>;
}) {
    const [retrying, setRetrying] = useState(false);
    const state = markOf(row, null);
    let end: ReactNode;
    if (!row) {
        end = live && installable(plugin)
            ? <button type="button" className="ox-btn ox-btn--primary ox-add__btn" onClick={onAdd} aria-label={`Add ${plugin.name}`}>Add</button>
            : <a className="ox-btn ox-add__btn" href={`/templates/${plugin.slug}`} target="_blank" rel="noreferrer">See it</a>;
    } else if (row.kind === "waiting" || row.kind === "adding") {
        end = <span className="ox-add__fitpct ox-num">{row.kind === "adding" ? `${Math.round(row.progress * 100)}%` : "Next"}</span>;
    } else if (row.kind === "added") {
        end = <span className="ox-add__intag"><Icon name="check" size={12} />In {name}</span>;
    } else if (row.kind === "failed") {
        end = (
            <button type="button" className="ox-btn ox-add__btn" disabled={retrying} onClick={() => { setRetrying(true); void onRetry().finally(() => setRetrying(false)); }}>
                {retrying ? "Trying…" : "Try again"}
            </button>
        );
    } else {
        const href = addHref(plugin);
        end = href ? <a className="ox-btn ox-add__btn" href={href}>Answer</a> : null;
    }
    const sub = row && (row.kind === "failed" || row.kind === "asks")
        ? <span className="ox-add__stop">{row.reason ?? "It stopped before it was in."}</span>
        : row && (row.kind === "waiting" || row.kind === "adding")
            ? <span className="ox-add__job">{stepWords(row.kind === "adding" ? row.step : null)}</span>
            : <span className="ox-add__job">{plugin.job}</span>;
    return (
        <li className="ox-add__fitrow" data-state={state}>
            <Mark slug={plugin.slug} state={state} progress={row?.progress ?? 0} />
            <span className="ox-add__fittext" role="status">
                <span className="ox-add__name">{plugin.name}</span>
                {sub}
            </span>
            {end}
        </li>
    );
}
