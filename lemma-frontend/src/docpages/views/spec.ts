/** A live view of a pod table, inside a page.
 *
 *  On disk it is a fenced block an agent can write as easily as a person:
 *
 *      ```lemma-view
 *      -- New leads
 *      -- spec: {"table":"leads","filters":[{"column":"status","op":"=","value":"New"}],...}
 *      SELECT "name", "status" FROM "leads" WHERE "status" = 'New' LIMIT 50
 *      ```
 *
 *  The SQL is the truth — it is what runs, through the pod's read-only query
 *  endpoint, with row security applied as the reader. The `spec` line is the
 *  builder's own record of how it made that SQL, so the builder can reopen it;
 *  SQL written by hand (a join, a window, anything) simply has no spec line
 *  and is edited as SQL.
 */

export type ViewOp = "=" | "!=" | ">" | ">=" | "<" | "<=" | "contains" | "empty" | "not empty";
export const VIEW_OPS: ViewOp[] = ["=", "!=", ">", ">=", "<", "<=", "contains", "empty", "not empty"];
export type Aggregate = "count" | "sum" | "avg" | "min" | "max";

export interface ViewSpec {
    table: string;
    /** Empty means every column. */
    columns: string[];
    filters: { column: string; op: ViewOp; value: string }[];
    sort: { column: string; dir: "asc" | "desc" } | null;
    /** Group rows by a column and summarise each group. */
    group: { column: string; aggregate: Aggregate; of: string | null } | null;
    limit: number;
}

export interface ViewBlock {
    title: string;
    spec: ViewSpec | null;
    sql: string;
}

export const MAX_ROWS = 1000;

const IDENT = /^[A-Za-z_][A-Za-z0-9_]*$/;

function ident(name: string): string {
    return '"' + name.replace(/"/g, '""') + '"';
}

/** Always a quoted string. PostgreSQL types an untyped quoted constant from
 *  the column it meets — so '500' works against a number, a boolean or text —
 *  where a bare 500 against a TEXT column is an operator error. */
function literal(value: string): string {
    return "'" + value.replace(/'/g, "''") + "'";
}

export function blankSpec(table: string): ViewSpec {
    return { table, columns: [], filters: [], sort: null, group: null, limit: 50 };
}

/** The SQL a spec means. Identifiers are quoted and values escaped, so a
 *  column called `order` or a value with a quote in it is still one query. */
export function buildSql(spec: ViewSpec): string {
    if (!IDENT.test(spec.table)) throw new Error("Not a table name: " + spec.table);
    const where = spec.filters
        .filter((one) => one.column)
        .map((one) => {
            const col = ident(one.column);
            switch (one.op) {
                case "contains": return col + "::text ILIKE " + literal("%" + one.value.replace(/[\\%_]/g, "\\$&") + "%");
                case "empty": return "(" + col + " IS NULL OR " + col + "::text = '')";
                case "not empty": return "(" + col + " IS NOT NULL AND " + col + "::text <> '')";
                default: return col + " " + one.op + " " + literal(one.value);
            }
        });
    const limit = Math.max(1, Math.min(MAX_ROWS, Math.floor(spec.limit) || 50));
    const whereSql = where.length ? " WHERE " + where.join(" AND ") : "";
    if (spec.group) {
        const g = ident(spec.group.column);
        const agg = spec.group.aggregate;
        const measure = agg === "count" || !spec.group.of
            ? "COUNT(*) AS count"
            : agg.toUpperCase() + "(" + ident(spec.group.of) + ") AS " + ident(agg + "_" + spec.group.of);
        const orderBy = agg === "count" || !spec.group.of ? "count" : ident(agg + "_" + spec.group.of);
        return "SELECT " + g + ", " + measure + " FROM " + ident(spec.table) + whereSql + " GROUP BY " + g + " ORDER BY " + orderBy + " DESC LIMIT " + limit;
    }
    const cols = spec.columns.length ? spec.columns.map(ident).join(", ") : "*";
    const order = spec.sort ? " ORDER BY " + ident(spec.sort.column) + " " + (spec.sort.dir === "desc" ? "DESC" : "ASC") : "";
    return "SELECT " + cols + " FROM " + ident(spec.table) + whereSql + order + " LIMIT " + limit;
}

export function readView(code: string): ViewBlock {
    const lines = code.replace(/\r/g, "").split("\n");
    let title = "";
    let spec: ViewSpec | null = null;
    const rest: string[] = [];
    for (const line of lines) {
        const comment = /^--\s?(.*)$/.exec(line.trim());
        if (comment && rest.length === 0) {
            const body = comment[1];
            const specLine = /^spec:\s*(\{.*\})\s*$/.exec(body);
            if (specLine) {
                try { spec = normalise(JSON.parse(specLine[1])); } catch { spec = null; }
            } else if (!title && body.trim()) {
                title = body.trim();
            }
            continue;
        }
        rest.push(line);
    }
    return { title, spec, sql: rest.join("\n").trim() };
}

export function writeView(view: ViewBlock): string {
    const lines: string[] = [];
    if (view.title.trim()) lines.push("-- " + view.title.trim().replace(/\n/g, " "));
    if (view.spec) lines.push("-- spec: " + JSON.stringify(view.spec));
    lines.push(view.sql.trim());
    return lines.join("\n");
}

function normalise(raw: unknown): ViewSpec | null {
    if (!raw || typeof raw !== "object") return null;
    const row = raw as Record<string, unknown>;
    if (typeof row.table !== "string" || !IDENT.test(row.table)) return null;
    const filters = Array.isArray(row.filters) ? row.filters : [];
    const sort = row.sort as { column?: unknown; dir?: unknown } | null;
    const group = row.group as { column?: unknown; aggregate?: unknown; of?: unknown } | null;
    return {
        table: row.table,
        columns: Array.isArray(row.columns) ? row.columns.filter((one): one is string => typeof one === "string") : [],
        filters: filters
            .map((one) => one as { column?: unknown; op?: unknown; value?: unknown })
            .filter((one) => typeof one.column === "string" && VIEW_OPS.includes(one.op as ViewOp))
            .map((one) => ({ column: one.column as string, op: one.op as ViewOp, value: String(one.value ?? "") })),
        sort: sort && typeof sort.column === "string" ? { column: sort.column, dir: sort.dir === "desc" ? "desc" : "asc" } : null,
        group: group && typeof group.column === "string"
            ? { column: group.column, aggregate: (["count", "sum", "avg", "min", "max"].includes(group.aggregate as string) ? group.aggregate : "count") as Aggregate, of: typeof group.of === "string" ? group.of : null }
            : null,
        limit: typeof row.limit === "number" ? row.limit : 50,
    };
}

/* ── the same spec, applied in memory ──────────────────────────────── */

/** What `buildSql` asks the server for, done over rows already in hand —
 *  for the sample, which has no SQL, and for checking the two agree. */
export function applySpec(rows: Record<string, unknown>[], spec: ViewSpec): Record<string, unknown>[] {
    const text = (value: unknown) => (value === null || value === undefined ? "" : String(value));
    const num = (value: unknown) => Number(value);
    const kept = rows.filter((row) => spec.filters.every((one) => {
        const value = row[one.column];
        switch (one.op) {
            case "contains": return text(value).toLowerCase().includes(one.value.toLowerCase());
            case "empty": return text(value) === "";
            case "not empty": return text(value) !== "";
            case "=": return text(value) === one.value;
            case "!=": return text(value) !== one.value;
            case ">": return num(value) > num(one.value);
            case ">=": return num(value) >= num(one.value);
            case "<": return num(value) < num(one.value);
            case "<=": return num(value) <= num(one.value);
        }
    }));
    const limit = Math.max(1, Math.min(MAX_ROWS, Math.floor(spec.limit) || 50));
    if (spec.group) {
        const { column, aggregate, of } = spec.group;
        const groups = new Map<string, Record<string, unknown>[]>();
        for (const row of kept) {
            const key = text(row[column]);
            groups.set(key, [...(groups.get(key) ?? []), row]);
        }
        const name = aggregate === "count" || !of ? "count" : aggregate + "_" + of;
        const out = [...groups.entries()].map(([key, members]) => {
            const values = of ? members.map((one) => num(one[of])).filter(Number.isFinite) : [];
            const measure = aggregate === "count" || !of ? members.length
                : aggregate === "sum" ? values.reduce((a, b) => a + b, 0)
                : aggregate === "avg" ? (values.length ? values.reduce((a, b) => a + b, 0) / values.length : null)
                : aggregate === "min" ? (values.length ? Math.min(...values) : null)
                : (values.length ? Math.max(...values) : null);
            return { [column]: key, [name]: measure };
        });
        return out.sort((a, b) => num(b[name]) - num(a[name])).slice(0, limit);
    }
    let out = kept;
    if (spec.sort) {
        const { column, dir } = spec.sort;
        out = [...out].sort((a, b) => {
            const left = a[column];
            const right = b[column];
            const both = Number.isFinite(num(left)) && Number.isFinite(num(right)) && text(left) !== "" && text(right) !== "";
            const order = both ? num(left) - num(right) : text(left).localeCompare(text(right));
            return dir === "desc" ? -order : order;
        });
    }
    out = out.slice(0, limit);
    if (spec.columns.length) out = out.map((row) => Object.fromEntries(spec.columns.map((column) => [column, row[column]])));
    return out;
}
