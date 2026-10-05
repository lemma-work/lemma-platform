"use client";

import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { readTourStep } from "../preview-mode";
import {
    addComment, assignBulk, columns, confirmLive, describe, initialFeedback, isFeedback, keepSpinner, mergeSpinner, people, replies, rules, skipAhead, summarize, theme,
    type Author, type Column, type Feedback, type PersonId, type Theme, type Viewer,
} from "./model";
import "./feedback-loop.css";

const STORE = "lemma-demo:kit-feedback:v1";
const KIT_FACE = "/teammates/loop-v1.png";
const viewers: Viewer[] = ["you", "dev", "sam"];
const queues: Record<Viewer, string> = { you: "Your queue · PM", dev: "Dev's queue · Engineer", sam: "Sam's queue · Support" };
const ink: Record<Column, string> = { nofix: "#a54232", progress: "#9a681f", merged: "#315d75", closed: "#34674e" };
/* The report page was last written at 09:05, so it reads the seed, not the live board. */
const SEED = initialFeedback().themes;
const AT_TWO = summarize(SEED);
const seeded = (id: string) => SEED.find(item => item.id === id)!;

function restore(): Feedback {
    try { const parsed: unknown = JSON.parse(sessionStorage.getItem(STORE) ?? "null"); if (isFeedback(parsed)) return parsed; } catch { /* A fresh start remains usable. */ }
    return initialFeedback();
}

function Face({ who, named = false }: { who: Author; named?: boolean }) {
    const name = who === "kit" ? "Kit" : people[who].name;
    if (who === "kit") return <img className="fl-face fl-face--kit" src={KIT_FACE} alt={named ? "" : name} />;
    return <span className={`fl-face fl-face--${who}`} role={named ? undefined : "img"} aria-label={named ? undefined : name} aria-hidden={named || undefined} title={name}>{people[who].initial}</span>;
}

function Mentions({ text }: { text: string }) {
    return <>{text.split(/(@kit\b)/i).map((part, index) => /^@kit$/i.test(part) ? <span key={index} className="fl-mention">@Kit</span> : part)}</>;
}

function Spark({ daily, color }: { daily: number[]; color: string }) {
    const width = 52, height = 16, max = Math.max(...daily, 1), step = width / Math.max(daily.length - 1, 1);
    const line = daily.map((value, index) => `${index ? "L" : "M"}${(index * step).toFixed(1)} ${(height - 2 - value / max * (height - 4)).toFixed(1)}`).join(" ");
    return <svg className="fl-spark" viewBox={`0 0 ${width} ${height}`} aria-hidden="true"><path d={`${line} L${width} ${height} L0 ${height}Z`} fill={color} fillOpacity=".1"/><path d={line} stroke={color} strokeWidth="1.5" fill="none" strokeLinejoin="round"/></svg>;
}

const Icon = {
    lock: <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true"><rect x="3.5" y="7" width="9" height="6.5" rx="1.6" stroke="currentColor" strokeWidth="1.4" fill="none"/><path d="M5.5 7V5.2a2.5 2.5 0 0 1 5 0V7" stroke="currentColor" strokeWidth="1.4" fill="none"/></svg>,
    page: <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5h5.5L12.5 5.5v8H4z" stroke="currentColor" strokeWidth="1.3" fill="none" strokeLinejoin="round"/><path d="M6 8.5h4.5M6 11h3" stroke="currentColor" strokeWidth="1.3"/></svg>,
    warn: <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.5l6 10.5H2z" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinejoin="round"/><path d="M8 6.8v2.8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/><circle cx="8" cy="11.4" r=".8" fill="currentColor"/></svg>,
    clock: <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.4" fill="none"/><path d="M8 4.8V8l2.2 1.4" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round"/></svg>,
    close: <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/></svg>,
};

function Owner({ owner }: { owner: PersonId | null }) {
    return owner ? <span className="fl-owner"><Face who={owner} named/>{people[owner].name}</span> : <span className="fl-owner fl-owner--none">{Icon.warn}No owner</span>;
}

type Item = { key: string; tone?: "done" | "info" | "yours"; kicker?: ReactNode; alert?: boolean; title: ReactNode; detail: ReactNode; actions?: ReactNode };

/** Kit's Feedback loop: every #feedback report sorted into themes, each theme
 *  carried from "no fix yet" to "people who reported it say it works". */
export function KitFeedbackLoop() {
    const [state, setState] = useState<Feedback>(restore);
    const [storageError, setStorageError] = useState(false);
    const [openId, setOpenId] = useState<string | null>(null);
    const [report, setReport] = useState(false);
    const [comment, setComment] = useState("");
    const [toast, setToast] = useState<{ id: number; text: string } | null>(null);
    const [flash, setFlash] = useState<{ id: string; n: number } | null>(null);
    const [seenActivity] = useState(() => Math.max(0, ...state.activity.map(entry => entry.id)));
    const scroller = useRef<HTMLDivElement>(null);
    const drawerBody = useRef<HTMLDivElement>(null);
    const closeButton = useRef<HTMLButtonElement>(null);
    const reportClose = useRef<HTMLButtonElement>(null);
    const returnFocus = useRef<HTMLElement | null>(null);
    const totals = summarize(state.themes);
    const open = openId ? theme(state, openId) ?? null : null;
    const dates = theme(state, "dates")!;
    const view = state.view;

    useEffect(() => { try { sessionStorage.setItem(STORE, JSON.stringify(state)); } catch { setStorageError(true); } }, [state]);
    /* The landing tour steps through the workspace this app sits in. Each step
       brings it back to its first screen; the visitor's actions stay, only
       where they were looking is reset. */
    useEffect(() => {
        const listen = (event: MessageEvent) => {
            if (event.origin !== window.location.origin || window.parent === window || event.source !== window.parent || readTourStep(event.data) === null) return;
            setOpenId(null); setReport(false); setComment(""); setToast(null);
            setState(previous => previous.view === "you" ? previous : { ...previous, view: "you" });
            scroller.current?.scrollTo({ top: 0 });
        };
        window.addEventListener("message", listen);
        return () => window.removeEventListener("message", listen);
    }, []);
    useEffect(() => {
        const escape = (event: KeyboardEvent) => {
            if (event.key !== "Escape") return;
            if (report) closeReport(); else if (openId) closeDrawer();
        };
        window.addEventListener("keydown", escape);
        return () => window.removeEventListener("keydown", escape);
    });
    useEffect(() => { if (openId) closeButton.current?.focus(); }, [openId]);
    useEffect(() => { if (report) reportClose.current?.focus(); }, [report]);
    useEffect(() => {
        if (!toast) return;
        const timer = window.setTimeout(() => setToast(null), 3600);
        return () => window.clearTimeout(timer);
    }, [toast]);
    useEffect(() => {
        if (!flash) return;
        document.getElementById("fl-card-" + flash.id)?.scrollIntoView({ block: "nearest", inline: "center", behavior: "smooth" });
        const timer = window.setTimeout(() => setFlash(null), 1400);
        return () => window.clearTimeout(timer);
    }, [flash]);

    function notify(text: string) { setToast({ id: Date.now(), text }); }
    function act(change: (previous: Feedback) => Feedback, after?: (next: Feedback) => void) {
        const next = change(state);
        if (next === state) return;
        setState(next);
        after?.(next);
    }
    function remember() { returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; }
    function restoreFocus() { const target = returnFocus.current; returnFocus.current = null; if (target?.isConnected) target.focus(); }
    function openDrawer(id: string) { if (!openId) remember(); setOpenId(id); setComment(""); }
    function closeDrawer() { setOpenId(null); setComment(""); restoreFocus(); }
    function openReport() { remember(); setReport(true); }
    function closeReport() { setReport(false); restoreFocus(); }
    function setView(next: Viewer) { setState(previous => ({ ...previous, view: next })); }

    const onConfirm = () => act(confirmLive, () => { setFlash({ id: "dates", n: Date.now() }); notify(`${replies.kit} replies sent in their threads. Sam has the ${replies.sam} Enterprise ones.`); });
    const onSkip = () => act(skipAhead, next => { const retried = theme(next, "dates")?.retried; setFlash({ id: "xls", n: Date.now() }); notify(`Thursday: ${retried?.ok} of ${retried?.asked} say it's fixed. The one failure was reopened, not lost.`); });
    const onAssign = (owner: PersonId) => act(previous => assignBulk(previous, owner), () => { setFlash({ id: "bulk", n: Date.now() }); notify(`${owner === "you" ? "You own" : people[owner].name + " owns"} Bulk edit missing now. LIN-257 created.`); });
    const onMerge = () => act(mergeSpinner, next => { setFlash({ id: "large", n: Date.now() }); notify(`Merged. Large imports time out now has ${theme(next, "large")?.reports} reports.`); });
    const onKeep = () => act(keepSpinner, () => { setFlash({ id: "spin", n: Date.now() }); notify("Kept separate. Kit noted it and assigned Dev."); });
    function onComment(event: FormEvent) {
        event.preventDefault();
        if (!openId || !comment.trim()) return;
        act(previous => addComment(previous, openId, comment), () => { setComment(""); requestAnimationFrame(() => drawerBody.current?.scrollTo({ top: drawerBody.current.scrollHeight, behavior: "smooth" })); });
    }

    const bulk = theme(state, "bulk");
    const spin = theme(state, "spin");
    const large = theme(state, "large");
    const items: Item[] = [];
    if (view === "you") {
        if (state.assigned) items.push({ key: "bulk", tone: "done", title: `Bulk edit missing assigned to ${state.assigned === "you" ? "you" : people[state.assigned].name}`, detail: `Kit ${state.assigned === "you" ? "" : `told ${people[state.assigned].name} and `}created LIN-257` });
        else if (bulk) items.push({ key: "bulk", alert: true, kicker: <>{Icon.warn}No owner</>, title: `Bulk edit missing · ${bulk.reports} reports`, detail: "No ticket. Two Enterprise renewals mention it (Sam).",
            actions: <><select className="fl-btn fl-select" aria-label="Assign an owner to Bulk edit missing" value="" onChange={event => event.target.value && onAssign(event.target.value as PersonId)}><option value="">Assign owner…</option><option value="dev">Dev</option><option value="alex">Alex</option><option value="you">Me</option></select><button className="fl-btn" onClick={() => openDrawer("bulk")}>Open</button></> });
        if (state.spinner === "merge") items.push({ key: "spin", tone: "done", title: "Merged into Large imports time out", detail: `${large?.reports} reports now. Dev was told.` });
        else if (state.spinner === "keep") items.push({ key: "spin", tone: "done", title: "Kept separate, assigned to Dev", detail: "Kit noted it and stopped suggesting the merge." });
        else if (spin) items.push({ key: "spin", kicker: <><Face who="kit" named/>Kit suggests</>, title: `Merge “${spin.name}” (${spin.reports}) into “Large imports time out”?`, detail: "Same file sizes, same upload step",
            actions: <><button className="fl-btn fl-btn--ink" onClick={onMerge}>Merge</button><button className="fl-btn" onClick={onKeep}>Keep separate</button></> });
        if (!state.live) items.push({ key: "live", tone: "info", kicker: <>{Icon.lock}Waiting on Dev</>, title: `Confirm #482 is live · posts ${replies.kit} retry replies`, detail: "Only the PR author can confirm. You can watch.",
            actions: <button className="fl-btn fl-btn--accent" onClick={() => setView("dev")}>View as Dev to confirm ›</button> });
    }
    if (view === "dev") {
        if (state.live) items.push({ key: "live", tone: "done", title: "Confirmed #482 live", detail: `Kit posted ${replies.kit} replies in their threads. Sam has ${replies.sam}.` });
        else items.push({ key: "live", tone: "yours", kicker: <>{Icon.lock}Approval · yours</>, title: "Confirm PR #482 is live for everyone", detail: `Posts ${replies.kit} retry replies in #feedback threads with the PR link`,
            actions: <><button className="fl-btn fl-btn--accent" onClick={onConfirm}>Confirm live</button><button className="fl-btn" onClick={() => openDrawer("dates")}>Review replies</button></> });
        if (large) items.push({ key: "large", alert: true, kicker: "P0 · assigned to you", title: `Large imports time out · ${large.reports} reports`, detail: "3 sample files attached · LIN-251", actions: <button className="fl-btn" onClick={() => openDrawer("large")}>Open</button> });
    }
    if (view === "sam") {
        items.push({ key: "replies", kicker: "Drafted for you", title: `${replies.sam} Enterprise replies for #482`, detail: `${replies.enterprise.join(", ")} · ${state.live ? "#482 is live, " : ""}you send these yourself`, actions: <button className="fl-btn fl-btn--ink" onClick={() => openDrawer("dates")}>Review</button> });
        items.push({ key: "repeat", alert: true, kicker: "Repeat reporter", title: "Northfield reported dates twice", detail: "Last on Tuesday by email", actions: <button className="fl-btn" onClick={() => openDrawer("dates")}>Open thread</button> });
    }
    const waiting = items.filter(item => !item.tone || item.tone === "yours").length;

    function card(item: Theme) {
        const right = item.retried && !(item.id === "dates" && !state.skipped)
            ? <span className="fl-retried"><span className="fl-meter" aria-hidden="true"><i style={{ width: `${Math.round(item.retried.ok / Math.max(item.retried.asked, 1) * 100)}%` }}/></span>{item.retried.ok}/{item.retried.asked} say it works</span>
            : item.id === "dates" && state.live ? <span>Retry asked · {item.retried?.ok ?? 0}/{item.reports}</span>
            : item.column === "merged" ? view === "sam" ? <span className="fl-owner" title="Only the PR author can confirm">{Icon.lock}Only Dev confirms</span> : <span>{replies.kit} ready · {replies.sam} with Sam</span>
            : null;
        const prTone = item.pr?.state === "Live" ? "green" : item.pr?.state === "Merged" ? "accent" : "blue";
        return <article key={item.id} id={`fl-card-${item.id}`} className="fl-card" data-column={item.column} data-selected={open?.id === item.id || undefined} data-flash={flash?.id === item.id || undefined}>
            <h3><button className="fl-card__open" aria-haspopup="dialog" onClick={() => openDrawer(item.id)}>{item.name}</button></h3>
            <div className="fl-card__row"><span className="fl-card__count"><b>{item.reports}</b>reports</span><Spark daily={item.daily} color={ink[item.column]}/></div>
            <q>{item.quote}</q>
            <div className="fl-tags">
                {item.p0 && <span className="fl-tag fl-tag--red">P0</span>}
                {item.badge === "new" && <span className="fl-tag fl-tag--amber">New · merge suggested</span>}
                {item.badge === "reopened" && <span className="fl-tag fl-tag--amber">Reopened from #482</span>}
                {item.ticket ? <span className="fl-tag fl-mono">{item.ticket}</span> : <span className="fl-tag fl-tag--red">No ticket</span>}
                {item.pr && <span className={`fl-tag fl-tag--${prTone} fl-mono`}>PR {item.pr.number} · {item.pr.state}</span>}
            </div>
            <footer><Owner owner={item.owner}/>{right}</footer>
            {item.id === "dates" && state.live && !state.skipped && <button className="fl-skip" onClick={onSkip}>Skip ahead a day ›</button>}
        </article>;
    }

    let lastDay = "";
    return <main className="fl" aria-label="Feedback loop">
        <header className="fl-top">
            <nav className="fl-crumb" aria-label="Breadcrumb"><span>Acme</span><i aria-hidden="true">/</i><span>Kit</span><i aria-hidden="true">/</i><span>Apps</span><i aria-hidden="true">/</i><b aria-current="page">Feedback loop</b></nav>
        </header>
        {storageError && <p className="fl-notice" role="status">Browser storage is unavailable. Your changes stay here until this page closes.</p>}
        <div className="fl-scroll" ref={scroller}>
            <div className="fl-app">
                <header className="fl-head">
                    <div className="fl-head__main">
                        <h1>Feedback loop</h1>
                        <p className="fl-by"><Face who="kit" named/>Built by Kit · used by <span className="fl-faces">{(["you", "dev", "sam", "alex"] as const).map(who => <Face key={who} who={who}/>)}</span></p>
                        <ul className="fl-health" aria-label="Health">
                            <li><i className="fl-pulse" aria-hidden="true"/>Last capture <b>2 min ago</b></li>
                            <li>{Icon.clock}Next reconcile <b>02:00</b></li>
                            <li>This month <b>{totals.reports}</b> reports · <b>{totals.themes}</b> themes</li>
                            <li>Loop closed this week <span className="fl-meter fl-meter--wide" aria-hidden="true"><i style={{ width: `${Math.round(totals.loop.ok / Math.max(totals.loop.asked, 1) * 100)}%` }}/></span><b>{totals.loop.ok}/{totals.loop.asked}</b></li>
                        </ul>
                    </div>
                    <div className="fl-head__side">
                        <div className="fl-seg" role="group" aria-label="View as">
                            <span aria-hidden="true">View as</span>
                            {viewers.map(who => <button key={who} aria-pressed={view === who} onClick={() => setView(who)}><Face who={who} named/>{who === "you" ? "You · PM" : people[who].name}</button>)}
                        </div>
                        <button className="fl-link" aria-haspopup="dialog" onClick={openReport}>{Icon.page}Feedback report, week 40</button>
                    </div>
                </header>

                <section className="fl-inbox" aria-labelledby="fl-inbox-title">
                    <header><h2 id="fl-inbox-title">Waiting on you {waiting > 0 && <span className="fl-count" aria-label={`${waiting} waiting`}>{waiting}</span>}</h2><span>{queues[view]}</span></header>
                    <div className="fl-items" aria-live="polite">
                        {items.map(item => <div key={item.key} className="fl-item" data-tone={item.tone}>
                            <div>
                                {item.kicker && <div className="fl-kicker" data-alert={item.alert || undefined}>{item.kicker}</div>}
                                <div className="fl-item__title">{item.tone === "done" && <span className="fl-tick" aria-hidden="true">✓ </span>}{item.title}</div>
                                <div className="fl-item__detail">{item.detail}</div>
                            </div>
                            {item.actions && <div className="fl-item__actions">{item.actions}</div>}
                        </div>)}
                        {items.length === 0 && <p className="fl-empty">Nothing waiting on you.</p>}
                    </div>
                </section>

                <div className="fl-grid">
                    <section className="fl-board" aria-label="Themes by fix status">
                        {columns.map(column => {
                            const cards = state.themes.filter(item => item.column === column.id);
                            return <div className="fl-col" key={column.id}>
                                <h2 className="fl-col__head"><span><i style={{ background: ink[column.id] }} aria-hidden="true"/>{column.name}</span><span className="fl-col__n">{cards.length}</span></h2>
                                <div className="fl-cards">{cards.map(card)}</div>
                            </div>;
                        })}
                    </section>
                    <aside className="fl-rail">
                        <section className="fl-box" aria-labelledby="fl-activity">
                            <h2 id="fl-activity">Activity <span>people and Kit</span></h2>
                            <ol className="fl-activity">
                                {state.activity.map(entry => {
                                    const heading = entry.day !== lastDay ? entry.day : null;
                                    lastDay = entry.day;
                                    return <li key={entry.id} data-fresh={entry.id > seenActivity || undefined}>
                                        {heading && <span className="fl-activity__day">{heading}</span>}
                                        <span className="fl-activity__row"><Face who={entry.by} named/><span>{entry.text}<time>{entry.at}</time></span></span>
                                    </li>;
                                })}
                            </ol>
                        </section>
                        <section className="fl-box" aria-labelledby="fl-rules">
                            <h2 id="fl-rules">Rules <span>taught by the team</span></h2>
                            <ul className="fl-rules">{rules.map(([rule, by]) => <li key={rule}>{rule}<em>{people[by].name}</em></li>)}</ul>
                        </section>
                    </aside>
                </div>
            </div>
        </div>

        {open && <>
            <div className="fl-scrim" onClick={closeDrawer} aria-hidden="true"/>
            <aside className="fl-drawer" role="dialog" aria-labelledby="fl-drawer-title">
                <header className="fl-drawer__head">
                    <div>
                        <h2 id="fl-drawer-title">{open.name}</h2>
                        <div className="fl-tags">
                            {open.p0 && <span className="fl-tag fl-tag--red">P0</span>}
                            <span className="fl-tag">{columns.find(column => column.id === open.column)?.name}</span>
                            {open.ticket && <span className="fl-tag fl-mono">{open.ticket}</span>}
                            {open.pr && <span className="fl-tag fl-mono">PR {open.pr.number} · {open.pr.state}</span>}
                            <Owner owner={open.owner}/>
                        </div>
                    </div>
                    <button ref={closeButton} className="fl-x" aria-label="Close theme" onClick={closeDrawer}>{Icon.close}</button>
                </header>
                <div className="fl-drawer__body" ref={drawerBody}>
                    <section className="fl-kit"><Face who="kit" named/><p><span className="fl-kit__name">Kit</span> · {describe(open)}</p></section>
                    <section className="fl-sec">
                        <h3>{open.reports} reports · where they came from</h3>
                        <div className="fl-sources" role="img" aria-label={`Slack ${open.sources[0]}, email ${open.sources[1]}, in app ${open.sources[2]}`}>{open.sources.map((count, index) => count > 0 && <i key={index} style={{ flex: count }} data-source={index}/>)}</div>
                        <div className="fl-legend" aria-hidden="true">{["Slack", "Email", "In app"].map((label, index) => <span key={label}><i data-source={index}/>{label} {open.sources[index]}</span>)}</div>
                    </section>
                    {open.id === "dates" && <section className="fl-sec">
                        <h3><label htmlFor="fl-reply">Retry reply · {state.live ? `posted to ${replies.kit} threads` : "draft, editable"}</label></h3>
                        {state.live ? <p className="fl-draft" id="fl-reply">{state.reply}</p>
                            : <textarea id="fl-reply" className="fl-draft" rows={4} value={state.reply} onChange={event => setState(previous => ({ ...previous, reply: event.target.value }))}/>}
                        <p className="fl-hint">{state.live ? `Posted by Kit at ${state.liveAt} · the ${replies.sam} Enterprise ones are with Sam` : `${replies.kit} threads via Kit · ${replies.sam} Enterprise via Sam · waits for Dev to confirm live`}</p>
                        {view === "dev" && !state.live && <button className="fl-btn fl-btn--accent" onClick={onConfirm}>Confirm live · post {replies.kit}</button>}
                        {view === "sam" && !state.live && <p className="fl-hint fl-hint--lock">{Icon.lock}Only Dev can mark this live. Your {replies.sam} Enterprise replies are ready to send.</p>}
                        {state.skipped && dates.retried && <div className="fl-report"><span className="fl-good">{dates.retried.ok} say it works</span> · 1 still sees text dates in .xls files, reopened as a new theme for Dev · {dates.retried.asked - dates.retried.ok - 1} not yet</div>}
                    </section>}
                    <section className="fl-sec">
                        <h3>Reports</h3>
                        {open.samples.length ? open.samples.map((sample, index) => <div className="fl-report" key={index}><div className="fl-report__meta"><span>{sample.who}</span><span>{sample.where}</span></div>{sample.text}</div>)
                            : <p className="fl-hint">All {open.reports} are in the Reports table.</p>}
                    </section>
                    <section className="fl-sec">
                        <h3>Thread</h3>
                        {open.comments.length ? open.comments.map((entry, index) => <div className="fl-comment" key={index}><Face who={entry.by} named/><div><div className="fl-comment__by">{entry.by === "kit" ? "Kit" : people[entry.by].name}<time>{entry.at}</time></div><p><Mentions text={entry.text}/></p></div></div>)
                            : <p className="fl-hint">No comments yet.</p>}
                    </section>
                </div>
                <form className="fl-compose" onSubmit={onComment}>
                    <label className="fl-visually-hidden" htmlFor="fl-comment">Comment as {people[view].name}</label>
                    <input id="fl-comment" value={comment} onChange={event => setComment(event.target.value)} placeholder={`Comment as ${people[view].name}, or @Kit…`} autoComplete="off"/>
                    <button className="fl-btn fl-btn--ink" disabled={!comment.trim()}>Post</button>
                </form>
            </aside>
        </>}

        {report && <div className="fl-modal" onClick={event => event.target === event.currentTarget && closeReport()}>
            <article className="fl-page" role="dialog" aria-modal="true" aria-labelledby="fl-page-title">
                <header>
                    <div><p className="fl-page__meta">{Icon.page}Page · written by Kit · updated Wed 09:05 · shared with Dev, Sam, Alex</p><h2 id="fl-page-title">Feedback report, week 40</h2></div>
                    <button ref={reportClose} className="fl-x" aria-label="Close report" onClick={closeReport}>{Icon.close}</button>
                </header>
                <div className="fl-page__body">
                    <p>{AT_TWO.reports} reports this month across {AT_TWO.themes} themes, {AT_TWO.overnight} of them new overnight. Captured as they landed: Slack {AT_TWO.share[0]}%, email {AT_TWO.share[1]}%, in app {AT_TWO.share[2]}%.</p>
                    <h3>The headline</h3>
                    <p>Large imports are our biggest unsolved problem. {seeded("large").reports} reports, up every day this week, 6 from paying accounts. Three people sent the files that fail; they’re attached to LIN-251.</p>
                    <aside className="fl-margin"><span>You · 09:03</span>Making this P0. Dev owns it.</aside>
                    <aside className="fl-margin"><span>Dev · 09:31</span>Repro’d with the 14k row file. Chunked upload path, fix likely Friday.</aside>
                    <h3>Overnight</h3>
                    <p>{seeded("spin").overnight} of the {AT_TWO.overnight} new reports describe a spinner that never ends on big uploads. I started a theme for them and suggested merging it into Large imports time out. That waits for you.</p>
                    <h3>Closing the loop</h3>
                    <p>{AT_TWO.loop.ok} of the {AT_TWO.loop.asked} people we asked to retry this week say it works. Duplicate rows ({seeded("dup").retried?.ok} of {seeded("dup").retried?.asked}) and column mapping ({seeded("map").retried?.ok} of {seeded("map").retried?.asked}) are confirmed closed. PR #482 (dates import as text) adds {seeded("dates").reports} more once Dev confirms it’s live.</p>
                    <h3>Nobody owns this yet</h3>
                    <p>Bulk edit, {seeded("bulk").reports} reports, two of them from Enterprise renewals (Sam). Needs a decision this week.</p>
                    <h3>What changed in how I sort</h3>
                    <p>“App feels stuck” on mobile now goes to Notifications, per Dev. 11 reports moved.</p>
                </div>
            </article>
        </div>}

        <button className="fl-ask" onClick={() => notify("In your workspace this opens a chat with Kit, with this app already in context.")}><Face who="kit" named/><span>Ask Kit</span></button>
        <div className="fl-toast" role="status" aria-live="polite">{toast && <p key={toast.id}>{toast.text}</p>}</div>
    </main>;
}
