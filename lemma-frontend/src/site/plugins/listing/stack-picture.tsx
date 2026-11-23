import Link from "next/link";
import { getBuilt, listed, nameOf, TABLES } from "../catalog";
import { faceSrc } from "../faces";
import { stackOf, standsOn, type Stack, type StackRow } from "../stack";
import s from "./listing.module.css";

/* Heights the picture is laid out on, in pixels: a row of plugins, the space
   between rows the lines cross, and the ground row of sources. The lines are
   drawn in a box of the same height, so they meet the boxes they join. */
const ROW = 64;
const GAP = 52;
const GROUND = 76;

function rowHeight(last: boolean): number {
    return last ? GROUND : ROW;
}

function tops(rows: StackRow[]): number[] {
    const out: number[] = [];
    let y = 0;
    rows.forEach((_, i) => {
        out.push(y);
        y += rowHeight(i === rows.length - 1) + GAP;
    });
    return out;
}

function heightOf(rows: StackRow[]): number {
    return rows.reduce((sum, _, i) => sum + rowHeight(i === rows.length - 1), 0) + GAP * Math.max(0, rows.length - 1);
}

/** What a plugin stands on, in words, for where the lines are not drawn. */
function onWords(slug: string, stack: Stack): string | null {
    const edges = standsOn(slug, stack);
    const brings = edges.filter((e) => e.kind === "brings").map((e) => nameOf(e.to));
    const reads = edges.filter((e) => e.kind === "reads").map((e) => nameOf(e.to));
    const parts = [brings.length ? `Brings ${listed(brings)}` : "", reads.length ? `Reads ${listed(reads)}` : ""].filter(Boolean);
    return parts.length ? parts.join(" · ") : null;
}

/** The lines of one column: from the bottom of each plugin to the top of
 *  what it stands on, solid where adding it brings that one along. */
function Lines({ rows, stack }: { rows: StackRow[]; stack: Stack }) {
    const top = tops(rows);
    const where = new Map<string, { x: number; row: number }>();
    rows.forEach((row, r) => row.nodes.forEach((n) => where.set(n.slug, { x: n.x, row: r })));
    const height = heightOf(rows);
    return (
        <svg className={s.stackLines} viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" style={{ height }} aria-hidden="true">
            {stack.edges.map((edge) => {
                const from = where.get(edge.from);
                const to = where.get(edge.to);
                if (!from || !to) return null;
                return (
                    <line
                        key={`${edge.from}-${edge.to}`}
                        x1={from.x * 100}
                        y1={top[from.row] + ROW}
                        x2={to.x * 100}
                        y2={top[to.row]}
                        vectorEffect="non-scaling-stroke"
                        data-kind={edge.kind}
                    />
                );
            })}
        </svg>
    );
}

function Node({ slug, stack }: { slug: string; stack: Stack }) {
    const on = onWords(slug, stack);
    return (
        <Link className={s.node} href={`/templates/${slug}`}>
            <img src={faceSrc(slug)} alt="" width={32} height={32} />
            <span>
                <span className={s.nodeName}>{nameOf(slug)}</span>
                {on && <small className={s.nodeOn}>{on}</small>}
            </span>
        </Link>
    );
}

function Column({ rows, stack, label }: { rows: StackRow[]; stack: Stack; label: string }) {
    const top = tops(rows);
    return (
        <div className={s.stackColumn} style={{ height: heightOf(rows) }} aria-label={label} role="group">
            <Lines rows={rows} stack={stack} />
            {rows.map((row, r) => {
                const last = r === rows.length - 1;
                const style = { top: top[r], height: rowHeight(last) };
                if (row.band) {
                    const slug = row.nodes[0].slug;
                    return (
                        <div key={slug} className={s.stackRow} style={style}>
                            <Link className={s.band} href={`/templates/${slug}`}>
                                <img src={faceSrc(slug)} alt="" width={32} height={32} />
                                <span className={s.nodeName}>{nameOf(slug)}</span>
                                <small>Reads what everything under it says needs you</small>
                            </Link>
                        </div>
                    );
                }
                if (last && row.nodes.length > 1) {
                    return (
                        <div key="ground" className={`${s.stackRow} ${s.ground}`} style={style}>
                            {row.nodes.map((n) => {
                                const plugin = getBuilt(n.slug);
                                const reads = plugin?.accounts.find((a) => !a.optional);
                                return (
                                    <Link key={n.slug} className={s.groundCell} href={`/templates/${n.slug}`}>
                                        <img src={faceSrc(n.slug)} alt="" width={32} height={32} />
                                        <span>
                                            <span className={s.nodeName}>{nameOf(n.slug)}</span>
                                            {reads && <small>Reads {listed(reads.any, "or")}</small>}
                                        </span>
                                    </Link>
                                );
                            })}
                        </div>
                    );
                }
                return (
                    <div key={row.nodes.map((n) => n.slug).join()} className={s.stackRow} style={style}>
                        {row.nodes.map((n) => (
                            <div key={n.slug} className={s.slot} style={{ left: `${n.x * 100}%` }}>
                                <Node slug={n.slug} stack={stack} />
                            </div>
                        ))}
                    </div>
                );
            })}
        </div>
    );
}

/** The shared tables of one plugin that some others read, in words. */
function sharedTables(slug: string, readers: string[]): string[] {
    const makes = getBuilt(slug)?.makes ?? [];
    const read = new Set(readers.flatMap((r) => getBuilt(r)?.reads.filter((t) => makes.includes(t)) ?? []));
    return [...read].map((t) => (TABLES[t]?.name ?? t).toLowerCase());
}

/** How the built plugins stack, from what each carries and reads. */
export function StackPicture() {
    const stack = stackOf();
    const across = Object.entries(stack.readAcross).filter(([, readers]) => readers.length);
    return (
        <figure className={s.stackFigure}>
            <div className={s.stack}>
                <Column rows={stack.main} stack={stack} label="Plugins built on Mail and Meetings" />
                <div className={s.beside}>
                    <p className={s.besideLabel}>Beside</p>
                    <Column rows={stack.beside} stack={stack} label="Plugins beside them" />
                    {across.map(([slug, readers]) => (
                        <p key={slug} className={s.across}>
                            {listed(readers.map(nameOf))} {readers.length > 1 ? "read" : "reads"} {nameOf(slug)}’s {listed(sharedTables(slug, readers))} too.
                        </p>
                    ))}
                </div>
            </div>
            <figcaption className={s.legend}>
                <span><i data-kind="brings" aria-hidden="true" />Adding it brings this along</span>
                <span><i data-kind="reads" aria-hidden="true" />Reads this when it is there</span>
            </figcaption>
        </figure>
    );
}
