"use client";

import { useEffect, useRef, useState } from "react";
import { cellText, rowLabel, type Row } from "./record-cache";
import { isFinished, rankedFields, type ColumnProfile, type FormChoice, type TableProfile } from "./profile";
import { compact, dayStart, readableNumber, whenever } from "./reading";

/** The shapes a table can take when the grid is not one of them.
 *
 *  Each of these draws the same rows the grid draws. What changes is which
 *  fact gets the space: the script rather than the column it sits in, the
 *  line rather than a hundred and twenty numbers, the deadline rather than the
 *  string it was stored as. None of them knows what the table is *for* — they
 *  are handed a column to build around and they build around it.
 */

export interface FormProps {
    rows: Row[];
    profile: TableProfile;
    choice: FormChoice;
    primaryKey: string;
    onOpen(row: Row): void;
    /** The whole row, in the dialog. */
    onEdit(row: Row): void;
    /** One field, where it is. Ticking a task and moving a card between piles
     *  are edits that must not cost a dialog and a retyped value. */
    onChange(row: Row, changes: Row): void;
    /** A new row, with whatever the place it was added from already implies —
     *  added under "Trialling", it starts out trialling. */
    onAdd(preset?: Row): void;
}

/** The columns worth putting beside the one the form is built around.
 *
 *  Ranked, so a card shows the state and the date rather than whichever two
 *  columns the table was declared with first. Prose is left out wherever it is
 *  not the thing the form is built around — a paragraph inside a board card is
 *  a board card the height of the screen. */
function supporting(profile: TableProfile, around: string | undefined, limit: number): ColumnProfile[] {
    return rankedFields(profile)
        .filter((c) => c.name !== around && c.role !== "key" && c.role !== "bookkeeping" && c.role !== "prose")
        .slice(0, limit);
}

/** How many rows a shape puts on the screen before it stops.
 *
 *  A table under a thousand rows is read whole, so that the shape chosen for
 *  it is chosen from all of it — and then every one of those rows was drawn.
 *  On a table of twenty-three columns that is some twenty thousand cells, and
 *  on a board it is a `<select>` per card; the tab stops responding long
 *  before anybody scrolls far enough to see why.
 *
 *  Reading all of it and drawing all of it are separate decisions, and only
 *  the first one has to be all. */
const PAGEFUL = 120;

function useCapped<T>(items: T[], size = PAGEFUL) {
    const [cap, setCap] = useState(size);
    return {
        shown: items.length > cap ? items.slice(0, cap) : items,
        hidden: Math.max(0, items.length - cap),
        more: () => setCap((was) => was + size),
    };
}

function MoreRows({ hidden, onMore }: { hidden: number; onMore(): void }) {
    if (hidden <= 0) return null;
    return <button className="form-more-rows" onClick={onMore}>Show more ({hidden} left)</button>;
}

/** The supporting fields, minus whichever one the heading already said.
 *
 *  `rowLabel` names a row after the first readable column it finds, which on a
 *  table with no `name` or `title` is an ordinary content column — and that
 *  column is then also in the list of facts to print beside the heading. The
 *  feed said "You already have the data" and then, directly underneath, "You
 *  already have the data". Compared by value and per row, because which column
 *  got picked is the row's business and not the table's. */
function besides(row: Row, fields: ColumnProfile[], heading: string): ColumnProfile[] {
    /* `rowLabel` shortens a long value and marks it with an ellipsis, so an
       exact comparison missed the very rows most likely to repeat: a card
       headed "Nemotron 3.5 L…" printed "Nemotron 3.5 Light…" again directly
       underneath, because the two strings were not equal. Compared from the
       front, and without the mark it added. */
    const plain = heading.replace(/…$/, "").trim().toLowerCase();
    return fields.filter((c) => {
        const value = row[c.name];
        if (value === null || value === undefined || value === "") return false;
        const text = cellText(value).replace(/…$/, "").trim().toLowerCase();
        if (!text) return false;
        return !(text.startsWith(plain) || plain.startsWith(text));
    });
}

/** A cell as this form should print it. The grid prints the stored string; a
 *  card has the space to print the number somebody would have written. */
function valueText(row: Row, column: ColumnProfile): string {
    const value = row[column.name];
    if (column.role === "number") return readableNumber(value) ?? cellText(value);
    return cellText(value);
}

function number(value: unknown): number {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : NaN;
}

/** A status, as the thing you change rather than the thing you read.
 *
 *  The options are the table's own: whatever the schema calls the column, the
 *  values a pod actually puts in it are written down in its rows. `＋` is there
 *  because a fixed list read off today's rows would otherwise be a list nobody
 *  can ever add a stage to. */
function StatusPicker({ column, row, onChange }: { column: ColumnProfile; row: Row; onChange(row: Row, changes: Row): void }) {
    const [adding, setAdding] = useState(false);
    const current = cellText(row[column.name]);

    if (adding) {
        return <input className="form-status form-status--new" autoFocus aria-label={"New " + column.name} defaultValue=""
            onKeyDown={(event) => {
                if (event.key === "Escape") setAdding(false);
                if (event.key !== "Enter") return;
                const next = event.currentTarget.value.trim();
                setAdding(false);
                if (next && next !== current) onChange(row, { [column.name]: next });
            }}
            onBlur={() => setAdding(false)}/>;
    }
    /* Wrapped, so the arrow is drawn here in the same ink as the label rather
       than by the platform at whatever size and inset it likes — which is what
       made the picker a different height and a different text inset from the
       tag sitting next to it. */
    return <span className="form-status">
        <select aria-label={column.name} value={current}
            onChange={(event) => {
                if (event.target.value === "\u0000new") { setAdding(true); return; }
                onChange(row, { [column.name]: event.target.value });
            }}>
            {column.values.includes(current) ? null : <option value={current}>{current}</option>}
            {column.values.map((value) => <option key={value} value={value}>{value}</option>)}
            <option value={"\u0000new"}>＋ another…</option>
        </select>
    </span>;
}

/* ---- feed -------------------------------------------------------------- */

export function FeedForm({ rows, profile, choice, primaryKey, onOpen, onEdit }: FormProps) {
    const prose = choice.around ?? "";
    const beside = supporting(profile, prose, 3);

    const capped = useCapped(rows);
    return <div className="form-feed">{capped.shown.map((row, i) => {
        const heading = rowLabel(row, primaryKey);
        return <article key={i}>
        <header>
            <div className="form-card-head">
                <button className="form-open" onClick={() => onOpen(row)}>{heading}</button>
                <button className="form-edit" onClick={() => onEdit(row)} aria-label={"Edit " + heading}>Edit</button>
            </div>
            <p className="form-meta">{besides(row, beside, heading).map((c) =>
                <span key={c.name} className={c.role === "enum" ? "form-tag" : undefined}>{valueText(row, c)}</span>)}</p>
        </header>
        {/* Whole, and wrapped as it was written. This column is why the row
            exists; a clamp on it is the grid again with more padding. */}
        <Prose text={String(row[prose] ?? "")}/>
    </article>;
    })}<MoreRows hidden={capped.hidden} onMore={capped.more}/></div>;
}

/** The text, clipped to a few lines, with a way to open it.
 *
 *  Showing it whole is the point of this shape, and fifteen rows of it whole
 *  is a page nobody can find anything on: every entry as tall as the screen,
 *  and no way to see what the next one is without scrolling past all of this
 *  one.
 *
 *  Whether to offer the control is measured, not guessed. Counting characters
 *  is the obvious proxy and it is wrong at every width but one — the same two
 *  hundred characters are four lines in a side pane and two on a wide screen,
 *  and a "More" under text that is plainly all there is a button that does
 *  nothing. So: does this element overflow the lines it is allowed, asked of
 *  the element, and asked again when its width changes. */
function Prose({ text }: { text: string }) {
    const [open, setOpen] = useState(false);
    const [clipped, setClipped] = useState(false);
    const node = useRef<HTMLParagraphElement | null>(null);

    useEffect(() => {
        const element = node.current;
        if (!element) return;
        /* Only while collapsed: opened, the element is exactly as tall as its
           content and would report itself as fitting, taking the way back with
           it. */
        const measure = () => { if (!open) setClipped(element.scrollHeight > element.clientHeight + 1); };
        measure();
        const watch = new ResizeObserver(measure);
        watch.observe(element);
        return () => watch.disconnect();
    }, [open, text]);

    return <>
        <p ref={node} className={"form-prose" + (open ? "" : " is-clipped")}>{text}</p>
        {clipped && <button className="form-more" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? "Less" : "More"}
        </button>}
    </>;
}

/* ---- chart ------------------------------------------------------------- */

/** One measure against time.
 *
 *  Small multiples and never two scales on one frame: signups in the tens and
 *  revenue in the thousands share an axis only by making one of them a flat
 *  line along the bottom, and the crossing point of two arbitrary scales is a
 *  fact about the scales.
 *
 *  One series per plot, so colour is not carrying identity and there is no
 *  palette to check — the accent is the ink of the mark and the title names
 *  what it is. */
function Plot({ points, label }: { points: { at: number; value: number; label: string }[]; label: string }) {
    const [hover, setHover] = useState<number | null>(null);
    const W = 720, H = 150, PAD = { top: 14, right: 16, bottom: 22, left: 44 };
    const plotted = points.filter((p) => Number.isFinite(p.value));
    if (plotted.length < 2) return null;

    const values = plotted.map((p) => p.value);
    const low = Math.min(...values), high = Math.max(...values);
    const span = high - low || Math.abs(high) || 1;
    const top = high + span * 0.12, bottom = Math.min(low - span * 0.12, low >= 0 ? 0 : low - span * 0.12);
    const x = (i: number) => PAD.left + (i / (plotted.length - 1)) * (W - PAD.left - PAD.right);
    const y = (v: number) => PAD.top + (1 - (v - bottom) / (top - bottom || 1)) * (H - PAD.top - PAD.bottom);

    const line = plotted.map((p, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(p.value).toFixed(1)).join(" ");
    const ticks = [bottom, (bottom + top) / 2, top];
    const peak = plotted.reduce((best, p, i) => (p.value > plotted[best].value ? i : best), 0);
    const shown = hover ?? peak;

    return <figure className="form-plot">
        <figcaption>{label}<span>{compact(plotted[plotted.length - 1].value)} latest · {compact(plotted[peak].value)} peak</span></figcaption>
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${label} over time, ${plotted.length} points, peak ${compact(plotted[peak].value)}`}
            onMouseLeave={() => setHover(null)}
            onMouseMove={(event) => {
                const box = event.currentTarget.getBoundingClientRect();
                const at = ((event.clientX - box.left) / box.width) * W;
                const index = Math.round(((at - PAD.left) / (W - PAD.left - PAD.right)) * (plotted.length - 1));
                setHover(Math.max(0, Math.min(plotted.length - 1, index)));
            }}>
            {ticks.map((t, i) => <g key={i}>
                <line className="form-plot__grid" x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)}/>
                <text className="form-plot__tick" x={PAD.left - 8} y={y(t) + 4} textAnchor="end">{compact(t)}</text>
            </g>)}
            <path className="form-plot__line" d={line}/>
            {hover !== null && <line className="form-plot__cross" x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={H - PAD.bottom}/>}
            <circle className="form-plot__dot" cx={x(shown)} cy={y(plotted[shown].value)} r={4.5}/>
            <text className="form-plot__edge" x={PAD.left} y={H - 6}>{plotted[0].label}</text>
            <text className="form-plot__edge" x={W - PAD.right} y={H - 6} textAnchor="end">{plotted[plotted.length - 1].label}</text>
        </svg>
        <p className="form-plot__read" role="status">{plotted[shown].label} · <strong>{compact(plotted[shown].value)}</strong></p>
    </figure>;
}

export function ChartForm({ rows, profile, choice }: FormProps) {
    const when = choice.around ?? "";
    const measures = profile.columns.filter((c) => c.role === "number");
    const ordered = [...rows].sort((a, b) => dayStart(a[when]) - dayStart(b[when]));
    return <div className="form-charts">{measures.map((c) => <Plot key={c.name} label={c.name}
        points={ordered.map((row) => ({
            at: Date.parse(String(row[when])),
            value: number(row[c.name]),
            label: String(row[when]).slice(0, 10),
        }))}/>)}</div>;
}

/* ---- checklist --------------------------------------------------------- */

export function ChecklistForm({ rows, profile, choice, primaryKey, onOpen, onChange, onAdd }: FormProps) {
    const when = choice.around ?? "";
    const state = profile.columns.find((c) => c.role === "boolean")
        ?? profile.columns.find((c) => c.role === "enum" && c.distinct <= 4);
    const beside = supporting(profile, when, 4).filter((c) => c.name !== state?.name && c.role !== "name");
    /* The same rule the choice was made with, and not a second opinion written
       out here. A local `done|complete|completed|closed|shipped` beside a
       chooser that asks only whether a state column exists is two rules that
       eventually disagree, and the visible half of that disagreement is a list
       reading "28 left of 28" for ever. */
    const settled = (row: Row): boolean => (state ? isFinished(row[state.name]) : false);
    const ordered = [...rows].sort((a, b) =>
        Number(settled(a)) - Number(settled(b)) || dayStart(a[when]) - dayStart(b[when]));
    const left = ordered.filter((r) => !settled(r)).length;

    const tickable = state?.role === "boolean";
    const capped = useCapped(ordered);
    return <div className="form-list">
        <p className="form-list__count">{left} left of {rows.length}
            <button className="form-add" onClick={() => onAdd()}>＋ Add</button></p>
        <ul>{capped.shown.map((row, i) => {
            const due = whenever(row[when]);
            const done = settled(row);
            const heading = rowLabel(row, primaryKey);
            return <li key={i} className={done ? "is-done" : undefined}>
                {/* The control, or nothing.
                    A read-only glyph beside a list of things to do is the most
                    inviting thing on the screen to click, and an empty one is a
                    promise the row cannot keep — where the state is a status
                    rather than a tick, the picker further along is the control
                    and this is only a box that never fills. */}
                {tickable && state
                    ? <input className="form-tick" type="checkbox" checked={done}
                        aria-label={heading} onChange={() => onChange(row, { [state.name]: !done })}/>
                    : done ? <span className="form-tick is-set" aria-hidden="true">✓</span>
                    : <span className="form-tick--none" aria-hidden="true"/>}
                <button className="form-open" onClick={() => onOpen(row)}>{heading}</button>
                {due && <span className={"form-due" + (!done && due.days < 0 ? " is-late" : "")}>{due.text}</span>}
                {/* On the second line with the rest of the detail, not as a
                    fourth thing in a three-column row — that is what put the
                    due date into the tick's 22px column, where it overflowed
                    sideways across the tags. */}
                <span className="form-meta">
                    {!tickable && state && <StatusPicker column={state} row={row} onChange={onChange}/>}
                    {besides(row, beside, heading).map((c) =>
                        <span key={c.name} className={c.role === "enum" ? "form-tag" : undefined}>{valueText(row, c)}</span>)}
                </span>
            </li>;
        })}</ul>
        <MoreRows hidden={capped.hidden} onMore={capped.more}/>
    </div>;
}

/* ---- timeline ---------------------------------------------------------- */

/** What happened, in the order it happened.
 *
 *  A history's rows all share one thing — when — and cards throw exactly that
 *  away, printing the date as the third field down on each of thirty tiles.
 *  Newest first, because the last thing that happened is the thing somebody
 *  came to see, and the date is said once per day rather than once per row. */
export function TimelineForm({ rows, profile, choice, primaryKey, onOpen, onEdit }: FormProps) {
    const when = choice.around ?? "";
    const beside = supporting(profile, when, 3);
    const ordered = [...rows].sort((a, b) => dayStart(b[when]) - dayStart(a[when]));
    const capped = useCapped(ordered);

    let lastDay = "";
    return <ol className="form-timeline">{capped.shown.map((row, i) => {
        const heading = rowLabel(row, primaryKey);
        const day = String(row[when] ?? "").slice(0, 10);
        const fresh = day !== lastDay;
        lastDay = day;
        const said = whenever(row[when]);
        return <li key={i}>
            {fresh && <p className="form-timeline__day">{day}<span>{said?.text}</span></p>}
            <div className="form-timeline__event">
                <div className="form-card-head">
                    <button className="form-open" onClick={() => onOpen(row)}>{heading}</button>
                    <button className="form-edit" onClick={() => onEdit(row)} aria-label={"Edit " + heading}>Edit</button>
                </div>
                <span className="form-meta">{besides(row, beside, heading).map((c) =>
                    <span key={c.name} className={c.role === "enum" ? "form-tag" : undefined}>{valueText(row, c)}</span>)}</span>
            </div>
        </li>;
    })}<li className="form-timeline__end"><MoreRows hidden={capped.hidden} onMore={capped.more}/></li></ol>;
}

/* ---- board ------------------------------------------------------------- */

export function BoardForm({ rows, profile, choice, primaryKey, onOpen, onChange, onAdd }: FormProps) {
    const by = choice.around ?? "";
    const axis = profile.columns.find((c) => c.name === by);
    const beside = supporting(profile, by, 2).filter((c) => c.role !== "name");
    const piles = usePiles(rows.map((row) => cellText(row[by])));
    return <div className="form-board">{piles.map((pile) => <Pile
        key={pile}
        pile={pile}
        rows={rows.filter((row) => cellText(row[by]) === pile)}
        by={by}
        axis={axis}
        beside={beside}
        primaryKey={primaryKey}
        onOpen={onOpen}
        onChange={onChange}
        onAdd={onAdd}
    />)}</div>;
}

/** One pile.
 *
 *  A component of its own so that it can hold its own cap: a board where one
 *  column has four hundred cards and the others have six should stop drawing
 *  the four hundred, not the six. Capping the board as a whole would have cut
 *  the short piles off at whatever was left over. */
function Pile({ pile, rows, by, axis, beside, primaryKey, onOpen, onChange, onAdd }: {
    pile: string;
    rows: Row[];
    by: string;
    axis?: ColumnProfile;
    beside: ColumnProfile[];
    primaryKey: string;
    onOpen(row: Row): void;
    onChange(row: Row, changes: Row): void;
    onAdd(preset?: Row): void;
}) {
    const capped = useCapped(rows, 50);
    return <section>
        <h3>{pile}<span>{rows.length}</span></h3>
        <ol>{capped.shown.map((row, i) => {
            const heading = rowLabel(row, primaryKey);
            return <li key={i}>
                <button className="form-open" onClick={() => onOpen(row)}>{heading}</button>
                {/* One line, with the mover on it. Three stacked lines for two
                    small facts left a card mostly empty and put six cards where
                    twelve fit. Moving a card is the one thing a board is for,
                    so the control sits with the facts rather than under them. */}
                <span className="form-meta">
                    {axis && <StatusPicker column={axis} row={row} onChange={onChange}/>}
                    {besides(row, beside, heading).map((c) =>
                        <span key={c.name} className={c.role === "enum" ? "form-tag" : undefined}>{valueText(row, c)}</span>)}
                </span>
            </li>;
        })}</ol>
        <MoreRows hidden={capped.hidden} onMore={capped.more}/>
        {/* Added here, it starts here. */}
        <button className="form-add" onClick={() => onAdd({ [by]: pile })}>＋ Add</button>
    </section>;
}

/** The piles, in a stable order.
 *
 *  First appearance, not alphabetical: a pipeline's stages have an order and
 *  it is not "Closed, In conversation, Trialling". But first appearance *of
 *  the current rows* is not stable — moving the first card from "todo" to
 *  "doing" made "doing" the first pile, and the whole board slid sideways
 *  under the hand that had just dropped something into it.
 *
 *  So the order is fixed the first time it is seen, and a value that turns up
 *  later joins the end rather than rearranging what is already there. A pile
 *  that empties keeps its place, which is what makes it somewhere to put
 *  things back. */
function usePiles(values: string[]): string[] {
    const order = useRef<string[]>([]);
    for (const value of values) if (!order.current.includes(value)) order.current = [...order.current, value];
    return order.current;
}

/* ---- cards ------------------------------------------------------------- */

export function CardsForm({ rows, profile, choice, primaryKey, onOpen, onEdit, onChange, onAdd }: FormProps) {
    /* A table with no name column still gets cards when there is nothing to
       scan, so there may be no column to build around — `rowLabel` finds a
       heading either way and every ranked field goes underneath it. */
    const named = choice.around;
    const beside = supporting(profile, named, named ? 4 : 6);
    const capped = useCapped(rows);
    return <div className="form-cards">{capped.shown.map((row, i) => {
        const heading = rowLabel(row, primaryKey);
        return <article key={i}>
            <div className="form-card-head">
                <button className="form-open" onClick={() => onOpen(row)}>{heading}</button>
                <button className="form-edit" onClick={() => onEdit(row)} aria-label={"Edit " + heading}>Edit</button>
            </div>
            <dl>{besides(row, beside, heading).map((c) => <div key={c.name}>
                <dt>{c.name}</dt>
                {/* A status is the field somebody came to change. Anywhere it
                    is shown at all, it is shown as the control. */}
                <dd>{c.role === "enum"
                    ? <StatusPicker column={c} row={row} onChange={onChange}/>
                    : valueText(row, c)}</dd>
            </div>)}</dl>
        </article>;
    })}
        <button className="form-cards__add form-add" onClick={() => onAdd()}>＋ Add</button>
        {capped.hidden > 0 && <div className="form-cards__more"><MoreRows hidden={capped.hidden} onMore={capped.more}/></div>}
    </div>;
}

/* ---- the one the view calls -------------------------------------------- */

export function TableForm(props: FormProps) {
    switch (props.choice.form) {
        case "feed": return <FeedForm {...props}/>;
        case "chart": return <ChartForm {...props}/>;
        case "checklist": return <ChecklistForm {...props}/>;
        case "timeline": return <TimelineForm {...props}/>;
        case "board": return <BoardForm {...props}/>;
        case "cards": return <CardsForm {...props}/>;
        case "grid": return null;
    }
}
