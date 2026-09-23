/** What a table is, read off its own rows.
 *
 *  Every table in this app renders as the same grid: the first five non-system
 *  columns, in the order the server happened to return them. That is how
 *  `content_ideas` — a table whose whole point is the script somebody wrote —
 *  came out as five squashed cells with the script clipped into three lines.
 *
 *  The fix is not a widget per column type. That was tried: asked to render
 *  `content_ideas` from its schema, a type-driven renderer gave `source` (five
 *  real values) a resizable textarea for the word "whatsapp". Knowing a column
 *  is text tells you nothing about whether it is the record or a footnote.
 *
 *  So this reads the *values*, and makes one decision for the whole table
 *  rather than one per column. The decision is small — six forms — and it
 *  abstains: anything unclear is a grid, which is what everything is today, so
 *  abstaining costs nothing.
 *
 *  The hard rule: this may know about **data** and never about **jobs**. "Most
 *  rows carry two hundred characters of prose" is a fact about values. "This is
 *  a support table, so the SLA goes top right" is a guess about somebody's
 *  work, and the moment one of those appears here this has become three hundred
 *  hand-maintained opinions wearing a switch statement.
 */

export type ColumnRole =
    | "key"
    /** About the row rather than in it: `created_at`, `pod_id`. */
    | "bookkeeping"
    /** What a row is called. */
    | "name"
    /** Free text long enough to be the point of the row. */
    | "prose"
    | "text"
    /** Few values, repeated: a status, a source, a stage. */
    | "enum"
    | "number"
    | "date"
    | "boolean"
    /** Points at another table. */
    | "link"
    /** An object or an array. */
    | "structured"
    /** Nothing in it, in every row seen. */
    | "empty";

export interface ColumnProfile {
    name: string;
    role: ColumnRole;
    /** Share of rows with a usable value, 0–1. */
    filled: number;
    /** Distinct values seen. */
    distinct: number;
    /** The values themselves, for a column few enough to be a category.
     *
     *  What makes a status editable without asking the server anything: the
     *  set of stages a table uses is written down in its own rows, whatever
     *  the schema calls the column. A pod that stores `stage` as free text
     *  still only ever puts four things in it. */
    values: string[];
    /** Median character length, for the text-ish roles. */
    length: number;
    longest: number;
    /** Share of values falling within a month either side of now, for dates.
     *
     *  A date column is not the same as a deadline. Ninety days of history and
     *  twenty-six tasks due on Friday are both "dated"; only one of them is a
     *  list of things to get through, and the difference is entirely where the
     *  dates sit relative to whoever is reading them. */
    near: number;
    /** Share of values falling today or later.
     *
     *  `near` is symmetric and cannot tell a deadline from a date stamp: ten
     *  releases shipped over the last six weeks are every bit as "within the
     *  month" as ten tasks due over the next six. It called them a checklist
     *  and reported ten shipped releases as "10 left". Work still to do points
     *  forwards; a history does not. */
    ahead: number;
}

export interface TableProfile {
    columns: ColumnProfile[];
    /** Rows this was read from. */
    seen: number;
    /** Every row in the table is here.
     *
     *  Rows arrive a page at a time and the datastore caps a query at a
     *  thousand, so `seen` is a floor and not a count unless this is true. Any
     *  rule that turns on smallness has to check it — "twenty-six tasks" and
     *  "the first twenty-six of four thousand" want opposite screens, and the
     *  rows alone cannot tell them apart.
     *
     *  The caller knows this two ways: no next page on the rows query, or a
     *  real count read alongside the page with `count(*) OVER ()`. It must not
     *  be inferred from a short page — the last page of a long table is short
     *  too. */
    complete: boolean;
}

/** The shapes a table can take. Six, plus the grid everything falls back to. */
export type Form = "grid" | "feed" | "chart" | "checklist" | "timeline" | "board" | "cards";

/** What to call each shape on screen.
 *
 *  "grid" is what the code has always called it and "table" is what a person
 *  calls it, and the person is the one reading the label. */
export const FORM_NAMES: Record<Form, string> = {
    grid: "table",
    feed: "reading view",
    chart: "chart",
    checklist: "checklist",
    timeline: "timeline",
    board: "board",
    cards: "cards",
};

export interface FormChoice {
    form: Form;
    /** The column the form is built around, where it has one. */
    around?: string;
    /** What this is, and only then why.
     *
     *  A layout nobody asked for has to say what it is — but it was saying
     *  things like "26 rows with no name to find them by, which is what a grid
     *  is for", which is the reasoning written out and leaves the reader to work
     *  backwards to "this is a table". Name the shape first, in the word a
     *  person would use for it, and add a reason only where the reason is not
     *  already on the screen: nobody needs telling why a board grouped by
     *  `stage` is grouped by `stage`. */
    because: string;
    /** Searching only the loaded rows is a lie on a table bigger than a page.
     *  True when the search box has to go to the server or say less. */
    searchIsPartial: boolean;
}

/* Columns that are about the row rather than in it. Kept in step with
   `record-cache.ts`, which needs the same list for a different reason. */
const BOOKKEEPING = new Set(["created_at", "updated_at", "creator_user_id", "sort_order", "pod_id", "user_id"]);
const NAMING = new Set(["name", "title", "label", "summary", "subject", "headline"]);

const LOOKS_LIKE_A_DATE = /^\d{4}-\d{2}-\d{2}([T ]|$)/;

/** Which way a date column points, as its own name says.
 *
 *  `ahead` — are these dates in the future — is the honest reading of the
 *  values, and it is wrong about the commonest work queue there is: a list
 *  where every next action is already overdue looks exactly like a list of
 *  things that happened. Twenty-eight accounts each with a `next_action_on`
 *  eight days past came out as a spreadsheet.
 *
 *  This is English rather than anything about anybody's job: `next_action_on`
 *  names something still to come and `shipped_at` names something done, the
 *  same way `title` names a title. A column that says neither falls back to
 *  reading the values. */
const POINTS_AHEAD = new Set(["due", "deadline", "expires", "expiry", "renews", "renewal", "starts", "scheduled", "schedule", "next", "followup", "follow", "target", "eta", "deliver"]);
const POINTS_BACK = new Set(["created", "updated", "modified", "shipped", "signed", "published", "sent", "closed", "completed", "resolved", "received", "logged", "added", "joined", "happened", "occurred", "last"]);

function dateDirection(name: string): "ahead" | "back" | "unsaid" {
    const words = name.toLowerCase().split(/[^a-z]+/).filter(Boolean);
    if (words.some((word) => POINTS_AHEAD.has(word))) return "ahead";
    if (words.some((word) => POINTS_BACK.has(word))) return "back";
    return "unsaid";
}

/** Columns whose name says they hold a state rather than a category.
 *
 *  A table can have four columns of four-or-fewer values — `source`, `segment`,
 *  `stage`, `owner` — and only one of them is the one that moves. Taking
 *  whichever was declared first offered a picker of `referral / telemetry`
 *  where the pipeline stage belonged. */
const STATEFUL = new Set(["status", "stage", "state", "phase", "step", "progress", "status_name"]);

/** Has this row reached the end of whatever it was doing?
 *
 *  Exported because the shape that shows a list and the rule that chose to show
 *  it have to agree on this, and they did not. The rule asked "is there a state
 *  column" and the list asked "which of these rows are done", from separate
 *  vocabularies — so a pipeline whose stages are `identified / not_now / paid`
 *  was made a checklist, and drew twenty-eight empty tick boxes under the words
 *  "28 left of 28". Nothing in it could ever be ticked.
 *
 *  Generic finishing words only. `paid` and `won` belong to somebody's sales
 *  process and `resolved` to somebody's support one; a table that ends in those
 *  is a table this does not get to have an opinion about, and it falls through
 *  to a shape that makes no promise about finishing. */
const FINISHED = /^(done|complete|completed|closed|resolved|cleared|finished|shipped|delivered|cancelled|canceled|archived|approved|rejected)$/i;

export function isFinished(value: unknown): boolean {
    return value === true || FINISHED.test(String(value ?? "").trim());
}
const LOOKS_LIKE_A_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Prose rather than a field. Set where a cell stops being readable on one line
 *  of a grid and starts being something a person has to open to use. */
const PROSE = 180;
/** Above this a column is describing the row rather than grouping it, so it is
 *  text and not a status — even if every value happens to repeat. */
const ENUM_VALUES = 8;
/** How far from now a date is still about the present. */
const MONTH = 31 * 24 * 60 * 60 * 1000;

function empty(value: unknown): boolean {
    return value === null || value === undefined || (typeof value === "string" && !value.trim());
}

function text(value: unknown): string | null {
    return typeof value === "string" && value.trim() ? value.trim() : null;
}

function median(numbers: number[]): number {
    if (numbers.length === 0) return 0;
    const sorted = [...numbers].sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[middle] : Math.round((sorted[middle - 1] + sorted[middle]) / 2);
}

const LOOKS_LIKE_AN_ADDRESS = /^[\d.:]+$/;

/** Does this column identify its row?
 *
 *  `NAMING` is a list of the words a column might be called, and real tables do
 *  not use them: a pipeline of twenty-eight companies keeps the thing each row
 *  is in a column called `company`, and the view told the reader it had "no
 *  title column" and gave them a spreadsheet. The list cannot be finished
 *  either — `account`, `vendor`, `candidate`, `matter`, `ticker` — so it should
 *  not be the only way to find one.
 *
 *  Read off the values instead: filled in everywhere, different on every row,
 *  short, and reading like language rather than like a machine. That last part
 *  matters — an audit log's `ip` is also filled, distinct and short, and a page
 *  of cards headed `10.2.0.0` is worse than the grid it replaced. */
function identifies(values: unknown[], lengths: number[], rowCount: number): boolean {
    if (rowCount < 4 || values.length < rowCount * 0.9) return false;
    if (new Set(values.map((v) => String(v))).size < rowCount * 0.9) return false;
    if (median(lengths) > 60) return false;
    /* Most of them, not all: one odd row should not cost a table its heading.
       And "has a letter in it" is not enough — a reference like
       `3f9056b4-1e2d-4132-b701-02b5091000` has plenty. What separates a name
       from an identifier is that a name is mostly not digits, or has a space
       in it, which no identifier does. */
    const language = values.filter((v) => {
        const text = String(v).trim();
        if (!/\p{L}/u.test(text) || LOOKS_LIKE_AN_ADDRESS.test(text) || LOOKS_LIKE_A_UUID.test(text)) return false;
        if (/\s/.test(text)) return true;
        return (text.replace(/\D/g, "").length / text.length) <= 0.3;
    });
    return language.length >= values.length * 0.8;
}

function roleOf(name: string, values: unknown[], primaryKey: string, lengths: number[], rowCount: number): ColumnRole {
    if (name === primaryKey) return "key";
    if (BOOKKEEPING.has(name)) return "bookkeeping";
    if (values.length === 0) return "empty";

    if (values.every((v) => typeof v === "boolean")) return "boolean";
    if (values.every((v) => typeof v === "object")) return "structured";

    /* A date is a string the server wrote, so this reads the value and not the
       column's name: `deadline`, `due`, `ship_on` and `when` are all the same
       thing and none of them says so. */
    if (values.every((v) => typeof v === "string" && LOOKS_LIKE_A_DATE.test(v))) return "date";

    /* Numeric strings count, because a datastore column can arrive either way —
       but never for the key, where the digits are an identity and not a
       quantity worth summing or plotting. */
    const numeric = values.every((v) => typeof v === "number" || (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))));
    if (numeric) return "number";

    if (name !== primaryKey && /_id$/.test(name)) return "link";
    if (values.every((v) => typeof v === "string" && LOOKS_LIKE_A_UUID.test(v))) return "link";

    const typical = median(lengths);
    if (typical >= PROSE) return "prose";

    /* Ahead of the status test, not after it. A `title` with eight values over
       ten rows satisfies every reasonable definition of a category and is
       still the thing the row is called — and a table that loses its heading
       to a pile of eight loses the heading everywhere, since the name is what
       every shape puts at the top of the row. */
    if (NAMING.has(name)) return "name";
    if (identifies(values, lengths, rowCount)) return "name";

    const distinct = new Set(values.map((v) => String(v))).size;
    /* Few enough to be a category, and fewer than there are rows.
       Deliberately not a rows-per-value ratio: at three rows per value a
       four-row table's `status` reads as free text, which costs it the shape
       it plainly asks for, since the board and the checklist both look for a
       status first. What this rule is for is ruling out a column where every
       row differs; the cap above rules out the rest. */
    if (distinct >= 2 && distinct <= ENUM_VALUES && distinct < values.length) return "enum";

    return "text";
}

export function profileTable(
    columns: { name: string; system?: boolean }[],
    rows: Record<string, unknown>[],
    options: { primaryKey?: string; complete?: boolean; now?: Date } = {},
): TableProfile {
    const primaryKey = options.primaryKey ?? "id";
    /* A parameter rather than `new Date()` inside, so what a table looks like
       does not depend on the day the test suite runs. */
    const now = (options.now ?? new Date()).getTime();
    const midnight = new Date(options.now ?? new Date());
    midnight.setHours(0, 0, 0, 0);
    const today = midnight.getTime();
    /* The declared columns, plus anything the rows carry that the schema did
       not mention. A row with a key nobody declared is still a thing on the
       screen. */
    const names = [...new Set([...columns.map((c) => c.name), ...rows.flatMap(Object.keys)])];

    return {
        seen: rows.length,
        complete: options.complete ?? false,
        columns: names.map((name) => {
            const present = rows.map((row) => row[name]).filter((v) => !empty(v));
            const lengths = present.map((v) => (text(v) ?? String(v)).length);
            const stamps = present.map((v) => Date.parse(String(v))).filter(Number.isFinite);
            const seen = [...new Set(present.map((v) => String(v)))];
            return {
                name,
                role: roleOf(name, present, primaryKey, lengths, rows.length),
                filled: rows.length ? present.length / rows.length : 0,
                distinct: seen.length,
                values: seen.length <= ENUM_VALUES ? seen : [],
                length: median(lengths),
                longest: lengths.length ? Math.max(...lengths) : 0,
                near: present.length ? stamps.filter((t) => Math.abs(t - now) <= MONTH).length / present.length : 0,
                ahead: present.length ? stamps.filter((t) => t >= today).length / present.length : 0,
            };
        }),
    };
}

/** How much a column tells you about the row it is on, most first.
 *
 *  Every shape here shows a few fields and hides the rest — the grid included,
 *  which shows five. So the question is never "can this form hold twenty-one
 *  columns", it is "which five", and the only answer available without this is
 *  *the first five the server listed*. Key order is not a ranking; on a real
 *  table it puts `created_at` first.
 *
 *  Dropped outright rather than ranked low:
 *
 *  - nothing in it in any row — an empty column is a label with a dash under it;
 *  - the same value in every row — `agent: "lemma-app/0.1"` on forty audit
 *    rows distinguishes none of them from any other, which is the entire job
 *    of a field on a card;
 *  - filled in almost nowhere, once there are enough rows to be sure of it.
 *
 *  The key sorts last rather than being dropped: on a table of three columns it
 *  is worth a look, and on a table of twenty the cut reaches it anyway. */
const ROLE_ORDER: ColumnRole[] = [
    "name", "enum", "date", "number", "boolean", "text", "link", "structured", "prose", "bookkeeping", "key", "empty",
];

/** Every column, best first, none left out.
 *
 *  What the grid wants. A shape that shows four fields has to leave things out
 *  and should leave out the right things; the grid is where somebody goes to
 *  see what is actually in the table, and a grid that has quietly dropped three
 *  columns is not that. So ordering and hiding come apart: this ranks, and only
 *  `rankedFields` below drops. */
export function orderedFields(profile: TableProfile): ColumnProfile[] {
    /* Stable inside a role, so columns that rank the same keep the order the
       table declared them in — which is the one thing that order is good for. */
    return profile.columns
        .map((column, at) => ({ column, at }))
        .sort((a, b) => ROLE_ORDER.indexOf(a.column.role) - ROLE_ORDER.indexOf(b.column.role) || a.at - b.at)
        .map((entry) => entry.column);
}

export function rankedFields(profile: TableProfile): ColumnProfile[] {
    return orderedFields(profile).filter((c) => {
        if (c.role === "empty") return false;
        if (profile.seen >= 3 && c.distinct <= 1) return false;
        if (profile.seen >= 20 && c.filled < 0.1) return false;
        return true;
    });
}

/** Columns a person is actually looking at: ranked, and without the ones that
 *  are about the row rather than in it. */
function content(profile: TableProfile): ColumnProfile[] {
    return rankedFields(profile).filter((c) => c.role !== "key" && c.role !== "bookkeeping");
}

/** How many rows to treat as "few". Above it a screen that shows every row at
 *  once stops being a screen. */
const FEW = 60;
/** Enough points for a line to mean anything. Below it a chart is decoration
 *  over a table somebody could have read. */
const SERIES = 24;
/** Below this there is nothing to scan, so the grid's density buys nothing. */
const SCANNABLE = 8;

/**
 *  The order is a precedence and not a menu: the first rule that matches wins,
 *  because more than one can be true at once and the earlier ones are about
 *  what a row *is* rather than how a set of them can be arranged.
 *
 *  Four of the six forms claim to be showing everything — a chart is the shape
 *  of the data, a board is where every row sits, a checklist is what is left to
 *  do, a set of cards is the set. Drawn over one page of a table that has forty
 *  more, each of them states something false and gives no hint that it has. So
 *  they all require `complete`, and a table still loading gets the grid, which
 *  says "rows loaded" and means it. Only the feed and the grid are honest about
 *  being a window onto something longer.
 */
export function chooseForm(profile: TableProfile): FormChoice {
    const fields = content(profile);
    const searchIsPartial = !profile.complete;
    const grid = (because: string): FormChoice => ({ form: "grid", because, searchIsPartial });

    if (profile.seen === 0 || fields.length === 0) return grid("Table — there is not enough here to build another view from.");

    /* The prose *is* the record. Nothing arranges around it, so this is first:
       a table with a script in it is a list of scripts however else it could
       be grouped. */
    const prose = fields.filter((c) => c.role === "prose" && c.filled >= 0.5)
        .sort((a, b) => b.length - a.length)[0];
    if (prose) {
        return {
            form: "feed",
            around: prose.name,
            because: "Reading view — " + prose.name + " is too long to read in a table.",
            searchIsPartial,
        };
    }

    const dates = fields.filter((c) => c.role === "date" && c.filled >= 0.8);
    const numbers = fields.filter((c) => c.role === "number");

    /* One row per moment, with something measured on it. The dates have to be
       nearly all different: a date column that repeats is a log of events that
       happened to share days, and plotting it silently sums things nobody
       asked to have summed. */
    const stamp = dates.find((c) => c.distinct >= profile.seen * 0.9);
    /* Dates and numbers and nothing else. Twenty-six tasks with a due date and
       an estimate on them also pass "enough rows, one per date, with something
       to plot" — and a chart of estimates against deadlines is a picture of
       nothing. A table that is measuring something has no name to call a row
       by and no state to put it in; the moment either appears, the rows are
       things rather than readings. */
    const measured = fields.every((c) => c.role === "date" || c.role === "number");
    if (profile.complete && stamp && measured && numbers.length > 0 && profile.seen >= SERIES) {
        return {
            form: "chart",
            around: stamp.name,
            because: "Chart — " + numbers.map((n) => n.name).join(" and ") + " over " + stamp.name + ".",
            searchIsPartial,
        };
    }

    /* Few enough to show at once, each with a date and a state: things to get
       through, which want ticking off and a countdown rather than sorting. */
    const ticked = fields.find((c) => c.role === "boolean");
    const statuses = fields
        .filter((c) => c.role === "enum" && c.distinct <= 4 && c.filled >= 0.8)
        /* A list that cannot shrink is not a checklist. Where none of a column's
           values is a finishing one, ticking things off is not what this table
           does, whatever else the column looks like. */
        .filter((c) => c.values.some(isFinished));
    const state = ticked ?? statuses.find((c) => STATEFUL.has(c.name.toLowerCase())) ?? statuses[0];
    /* A boolean is unambiguously a completion: true and false are done and not
       done whatever the column is called, so dates that have all passed are
       simply a list that is nearly finished.
       An enum is not. `kind` — feature, fix, rename — satisfies every
       structural test for a status and is a category, and the only honest
       signal left that its rows are work rather than record is that some of
       them are still ahead. */
    const due = dates.find((c) => {
        if (c.near < 0.5) return false;
        const points = dateDirection(c.name);
        if (points === "back" && !ticked) return false;
        return ticked !== undefined || points === "ahead" || c.ahead >= 0.2;
    });
    if (profile.complete && profile.seen <= FEW && due && state) {
        return {
            form: "checklist",
            around: due.name,
            because: "Checklist, sorted by " + due.name + ".",
            searchIsPartial,
        };
    }

    /* Things that happened, each at its own moment.
     *
     *  Not a chart, because there is nothing measured to plot — the rows are
     *  events with names on them. Not a checklist, because a checklist is work
     *  still to come and this is a record of what already happened; the two are
     *  told apart by where the dates sit, which is the same reading the
     *  checklist made just above. And not cards, which was where this landed
     *  before and which throws away the one thing every row has in common. */
    const happened = dates.find((c) => c.distinct >= profile.seen * 0.6 && dateDirection(c.name) !== "ahead");
    const titled = fields.find((c) => c.role === "name" && c.filled >= 0.8);
    if (profile.complete && happened && titled && profile.seen >= 3 && profile.seen <= 200) {
        return {
            form: "timeline",
            around: happened.name,
            because: "Timeline, newest first by " + happened.name + ".",
            searchIsPartial,
        };
    }

    /* A handful of states across a lot of rows: piles, and moving between
       them.
       One axis, not a pick from several. An audit log has an actor, an action,
       a resource, a status and a region all sitting in four-or-fewer values,
       and piling forty rows up by whichever of those was declared first says
       nothing — none of them is what the row *is*. Where a table has one or two
       such columns, the first of them is the axis. */
    const enums = fields.filter((c) => c.role === "enum" && c.distinct >= 2 && c.distinct <= 6 && c.filled >= 0.9);
    /* A column named for a state is the axis when there is one — a table can
       carry `source`, `segment`, `stage` and `owner` and only `stage` is the
       thing that moves. Failing that, one or two candidates means the first is
       the axis and five means none of them is. */
    const stage = enums.find((c) => STATEFUL.has(c.name.toLowerCase())) ?? (enums.length <= 2 ? enums[0] : undefined);
    /* And a board is cards in piles, so it needs what a card needs: something
       to write at the top of each one. Forty log lines grouped by `status` is
       two piles of anonymous rows. */
    const heads = fields.some((c) => c.role === "name" && c.filled >= 0.8);
    /* Not a row count. Four rows with a status are four rows in piles — that is
       what the status is *for* — so "too few to scan down a column" would
       refuse the one shape those four rows plainly ask for. What a board does
       need is that the piles are piles: where every row sits in a value of its
       own there is nothing being grouped, only a list with a heading over each
       item. */
    if (profile.complete && stage && heads && profile.seen >= 3 && stage.distinct < profile.seen) {
        return {
            form: "board",
            around: stage.name,
            because: "Board, grouped by " + stage.name + ".",
            searchIsPartial,
        };
    }

    /* Named things, or too few rows for a grid to be doing anything.
     *
     *  A grid earns its density by being scanned and compared down a column.
     *  Three rows cannot be scanned — a grid of three is three rows of five
     *  truncated cells and a lot of white space to the right, where the same
     *  three as cards show every field they have. So smallness is a reason to
     *  choose this shape, not the reason to refuse to choose one, which is what
     *  it was.
     *
     *  Past that the name is what decides it: rows with names are things, and
     *  things are looked *for*; rows without them are events, and forty events
     *  are read down a column. */
    const named = fields.find((c) => c.role === "name" && c.filled >= 0.8);
    if (profile.complete && (profile.seen <= SCANNABLE || (named && profile.seen <= 200))) {
        return {
            form: "cards",
            around: named?.name,
            because: named
                ? "Cards, one per " + named.name + "."
                : "Cards — only " + profile.seen + " rows, too few for a table to help.",
            searchIsPartial,
        };
    }

    /* Three different reasons land here and they are not interchangeable. A
       sentence that says "no name to find them by" about a table of people
       with names is worse than no sentence: it is the view being confidently
       wrong about the thing it just looked at. */
    /* No reason at all for this one. "Only 100 rows are loaded so far" says
       nothing a reader can act on, repeats the count the footer is already
       showing, and sounds like an apology for a table that is working fine. A
       table is the ordinary way to look at rows; it does not have to explain
       itself for being one. */
    if (!profile.complete) return grid("Table view.");
    if (named) return grid("Table — " + profile.seen + " rows is too many to show as cards.");
    return grid("Table — these rows have no title column.");
}

/** A row on its own page.
 *
 *  The same reading, turned sideways. A table asks "which four of these columns
 *  go on a card"; a record page fits all of them and still has to
 *  decide what a person looks at first, which is the same question with a
 *  different budget — and it was answered with `Object.entries(row)`, the order
 *  the server happened to serialise them in, every value in an identical grey
 *  box. A paragraph of notes and a uuid got the same two lines each.
 *
 *  Four bands, because a row has four kinds of thing in it: what state it is
 *  in, what it is, what somebody wrote about it, and the bookkeeping that is
 *  about the row rather than in it.
 */
export interface RecordLayout {
    /** The one column somebody came to change. */
    state?: ColumnProfile;
    /** Short facts, ranked, for a compact list near the top. */
    facts: ColumnProfile[];
    /** Long text. Each gets its own block and its full width. */
    prose: ColumnProfile[];
    /** The key, the timestamps: kept, and kept out of the way. */
    quiet: ColumnProfile[];
}

export function layoutRecord(profile: TableProfile, skip: string[] = []): RecordLayout {
    const ignored = new Set(skip);
    const ordered = orderedFields(profile).filter((c) => !ignored.has(c.name));
    const statuses = ordered.filter((c) => c.role === "enum" && c.distinct <= 6);
    const state = statuses.find((c) => STATEFUL.has(c.name.toLowerCase())) ?? statuses[0];

    return {
        state,
        prose: ordered.filter((c) => c.role === "prose"),
        quiet: ordered.filter((c) => c.role === "bookkeeping" || c.role === "key"),
        facts: ordered.filter((c) =>
            c !== state
            && c.role !== "prose"
            && c.role !== "bookkeeping"
            && c.role !== "key"),
    };
}
