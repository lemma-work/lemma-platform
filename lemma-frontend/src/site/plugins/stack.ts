import { BUILT, type BuiltPlugin } from "./catalog";

/* How the built plugins stack, worked out from what they carry and the shared
   tables they read, never drawn by hand:

   - Underneath are the sources: plugins others carry that carry nothing
     themselves (Mail and Meetings, which read the inbox and the calendar).
   - Every plugin that reaches a source, by carrying it or by reading a table
     one makes, stands in rows above it, one row higher than the highest thing
     it stands on.
   - A plugin that reads what all the others write (Today, from the day's
     shared list) spans the top.
   - The rest stand beside, stacked the same way among themselves (Brand kit,
     and Deck maker on its brand). */

/** "brings": adding the upper one adds the lower one. "reads": the upper one
 *  reads what the lower one writes, when both are there. */
export type EdgeKind = "brings" | "reads";

export interface StackEdge {
    from: string;
    to: string;
    kind: EdgeKind;
}

export interface StackNode {
    slug: string;
    /** Where its centre sits across its column, 0 to 1. */
    x: number;
}

export interface StackRow {
    /** A row that spans the column: it reads from everything beneath it. */
    band: boolean;
    nodes: StackNode[];
}

export interface Stack {
    /** The column standing on the sources, top row first. */
    main: StackRow[];
    /** The column of plugins that do not reach a source, top row first. */
    beside: StackRow[];
    /** The lines to draw: from a node to what it stands on in its column. */
    edges: StackEdge[];
    /** Main-column plugins that read from a plugin beside, by that plugin. */
    readAcross: Record<string, string[]>;
}

/** What one plugin stands on, by slug, with how. */
function edgesOf(plugin: BuiltPlugin, all: readonly BuiltPlugin[]): StackEdge[] {
    const out = new Map<string, EdgeKind>();
    for (const slug of plugin.carriesDirect) out.set(slug, "brings");
    for (const table of plugin.reads) {
        const maker = all.find((p) => p.makes.includes(table));
        if (maker && maker.slug !== plugin.slug && !out.has(maker.slug)) out.set(maker.slug, "reads");
    }
    if (plugin.day === "reads") {
        for (const writer of all) {
            if (writer.day === "writes" && writer.slug !== plugin.slug && !out.has(writer.slug)) {
                out.set(writer.slug, "reads");
            }
        }
    }
    return [...out].map(([to, kind]) => ({ from: plugin.slug, to, kind }));
}

/** The smallest gap kept between two centres in a row, as a share of the
 *  column, so names never overlap. */
const GAP = 0.3;

/** Centres for a row: as near as can be to where each wants to be (over what
 *  it stands on), in that order, at least `GAP` apart, inside the column. */
function spread(wanted: Array<{ slug: string; x: number; order: number }>): StackNode[] {
    const sorted = [...wanted].sort((a, b) => a.x - b.x || a.order - b.order);
    const gap = Math.min(GAP, 1 / sorted.length);
    const xs = sorted.map((w) => w.x);
    for (let i = 1; i < xs.length; i++) xs[i] = Math.max(xs[i], xs[i - 1] + gap);
    const shift = sorted.reduce((sum, w) => sum + w.x, 0) / sorted.length - xs.reduce((a, b) => a + b, 0) / xs.length;
    let placed = xs.map((x) => x + shift);
    const low = gap / 2 - placed[0];
    if (low > 0) placed = placed.map((x) => x + low);
    const high = placed[placed.length - 1] - (1 - gap / 2);
    if (high > 0) placed = placed.map((x) => x - high);
    return sorted.map((w, i) => ({ slug: w.slug, x: Math.round(placed[i] * 1000) / 1000 }));
}

export function stackOf(all: readonly BuiltPlugin[] = BUILT): Stack {
    const edges = new Map(all.map((p) => [p.slug, edgesOf(p, all)]));
    const carried = new Set(all.flatMap((p) => p.carriesDirect));
    const sources = new Set(all.filter((p) => p.carriesDirect.length === 0 && carried.has(p.slug)).map((p) => p.slug));
    const order = new Map(all.map((p) => [p.slug, p.position]));

    const reaches = (slug: string, seen = new Set<string>()): boolean => {
        if (sources.has(slug)) return true;
        if (seen.has(slug)) return false;
        seen.add(slug);
        return (edges.get(slug) ?? []).some((e) => reaches(e.to, seen));
    };
    const inMain = new Set(all.filter((p) => reaches(p.slug)).map((p) => p.slug));
    const sameColumn = (a: string, b: string) => inMain.has(a) === inMain.has(b);

    /* Sources are the ground; their own reads (Mail reads Meetings' meetings
       and Meetings reads Mail's people) are a pair, not a stack. */
    const tiers = new Map<string, number>();
    const tierOf = (slug: string, trail = new Set<string>()): number => {
        const known = tiers.get(slug);
        if (known !== undefined) return known;
        if (sources.has(slug) || trail.has(slug)) return 0;
        trail.add(slug);
        const under = (edges.get(slug) ?? []).filter((e) => sameColumn(slug, e.to));
        const tier = under.length ? 1 + Math.max(...under.map((e) => tierOf(e.to, trail))) : 0;
        trail.delete(slug);
        tiers.set(slug, tier);
        return tier;
    };
    for (const p of all) tierOf(p.slug);

    const column = (members: string[]): { rows: StackRow[]; drawn: StackEdge[] } => {
        const ordered = [...members].sort((a, b) => (order.get(a) ?? 0) - (order.get(b) ?? 0));
        const height = Math.max(-1, ...ordered.map((s) => tiers.get(s) ?? 0)) + 1;
        const spans = new Set(
            ordered.filter((slug) => {
                const under = new Set((edges.get(slug) ?? []).map((e) => e.to));
                const others = ordered.filter((s) => s !== slug);
                return others.length > 1 && others.every((s) => under.has(s));
            }),
        );
        const x = new Map<string, number>();
        const rows: StackRow[] = [];
        for (let tier = 0; tier < height; tier++) {
            const here = ordered.filter((s) => (tiers.get(s) ?? 0) === tier);
            const band = here.length === 1 && spans.has(here[0]);
            const wanted = here.map((slug, i) => {
                const under = (edges.get(slug) ?? []).filter((e) => x.has(e.to)).map((e) => x.get(e.to) ?? 0.5);
                const fallback = (i + 0.5) / here.length;
                return {
                    slug,
                    x: tier === 0 || !under.length ? fallback : under.reduce((a, b) => a + b, 0) / under.length,
                    order: order.get(slug) ?? 0,
                };
            });
            const nodes = band ? [{ slug: here[0], x: 0.5 }] : spread(wanted);
            for (const node of nodes) if (!band) x.set(node.slug, node.x);
            rows.unshift({ band, nodes });
        }
        const drawn = ordered.flatMap((slug) =>
            spans.has(slug) || sources.has(slug)
                ? []
                : (edges.get(slug) ?? []).filter((e) => members.includes(e.to) && x.has(e.to)),
        );
        return { rows, drawn };
    };

    const mainSlugs = all.filter((p) => inMain.has(p.slug)).map((p) => p.slug);
    const besideSlugs = all.filter((p) => !inMain.has(p.slug)).map((p) => p.slug);
    const main = column(mainSlugs);
    const beside = column(besideSlugs);

    const readAcross: Record<string, string[]> = {};
    for (const slug of besideSlugs) {
        readAcross[slug] = mainSlugs.filter((m) => (edges.get(m) ?? []).some((e) => e.to === slug && e.kind === "reads") && !main.rows.some((r) => r.band && r.nodes[0].slug === m));
    }

    return { main: main.rows, beside: beside.rows, edges: [...main.drawn, ...beside.drawn], readAcross };
}

/** What a plugin stands on in the stack, for a line under its name. */
export function standsOn(slug: string, stack: Stack): StackEdge[] {
    return stack.edges.filter((e) => e.from === slug);
}
