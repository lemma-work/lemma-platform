export type PersonId = "you" | "dev" | "sam" | "alex";
export type Author = PersonId | "kit";
export type Viewer = "you" | "dev" | "sam";
export type Column = "nofix" | "progress" | "merged" | "closed";
export type PrState = "In review" | "Merged" | "Live";
export type Report = { who: string; where: string; text: string };
export type Comment = { by: Author; at: string; text: string };
/** Reports are split by where they were captured: Slack, email, in app. */
export type Sources = [number, number, number];
export type Theme = {
    id: string; name: string; column: Column; reports: number; overnight: number; daily: number[]; sources: Sources;
    owner: PersonId | null; p0?: boolean; ticket: string | null; pr: { number: string; state: PrState } | null;
    quote: string; summary: string; samples: Report[]; comments: Comment[];
    /** People asked to retry, and how many said it works. */
    retried?: { ok: number; asked: number };
    badge?: "new" | "reopened";
};
export type Activity = { id: number; day: string; by: Author; at: string; text: string };
export type Feedback = {
    view: Viewer; day: string; clock: number;
    live: boolean; liveAt: string | null; skipped: boolean;
    assigned: PersonId | null; spinner: "merge" | "keep" | null;
    reply: string; themes: Theme[]; activity: Activity[];
};

export const people: Record<PersonId, { name: string; initial: string; role: string }> = {
    you: { name: "You", initial: "Y", role: "PM" },
    dev: { name: "Dev", initial: "D", role: "Engineer" },
    sam: { name: "Sam", initial: "S", role: "Support" },
    alex: { name: "Alex", initial: "A", role: "Engineer" },
};
export const columns: { id: Column; name: string }[] = [
    { id: "nofix", name: "No fix yet" }, { id: "progress", name: "Fix in progress" }, { id: "merged", name: "Merged" }, { id: "closed", name: "Closed" },
];
export const rules: [string, PersonId][] = [
    ["Every #feedback message is captured as it lands", "you"],
    ["Retry replies wait for the PR author to confirm it's live", "you"],
    ["Enterprise accounts hear from their CSM", "sam"],
    ["On mobile, “stuck” means Notifications, not timeouts", "dev"],
    ["Priority is reports × paying accounts", "you"],
];
/** #482's retry replies: Kit posts in #feedback threads, Sam emails the Enterprise accounts. */
export const replies = { kit: 19, sam: 3, enterprise: ["Northfield", "Brightpath", "Kite Labs"] };

const closed = (id: string, name: string, reports: number, daily: number[], sources: Sources, owner: PersonId, ticket: string, pr: string, ok: number, asked: number, quote: string, summary: string): Theme =>
    ({ id, name, column: "closed", reports, overnight: 0, daily, sources, owner, ticket, pr: { number: pr, state: "Live" }, retried: { ok, asked }, quote, summary, samples: [], comments: [] });

function seedThemes(): Theme[] {
    return [
        { id: "large", name: "Large imports time out", column: "nofix", reports: 41, overnight: 0, daily: [2, 3, 4, 5, 7, 9, 11], sources: [30, 8, 3], owner: "dev", p0: true, ticket: "LIN-251", pr: null,
            quote: "14k rows, spinner for 10 min then a timeout. Second time this week.",
            summary: "{n} reports since 8 Sep, rising every day. 6 from paying accounts. Imports over ~10k rows time out at the upload step. 3 people sent their files, attached to LIN-251.",
            samples: [{ who: "@lena.p", where: "Slack · Pro", text: "14k rows, spinner for 10 min then a timeout. Second time this week." }, { who: "Kite Labs", where: "email · Enterprise", text: "Our nightly import of 22k rows fails every time now." }, { who: "@oskar", where: "in app · Free", text: "stuck at 99% on a big CSV" }],
            comments: [{ by: "you", at: "09:03", text: "@Kit timeouts go to Dev, P0" }, { by: "kit", at: "09:03", text: "Assigned to Dev, P0. Attached the 3 sample files people sent." }, { by: "dev", at: "09:31", text: "Repro'd with the 14k row file. Looking at the chunked upload path." }] },
        { id: "bulk", name: "Bulk edit missing", column: "nofix", reports: 26, overnight: 2, daily: [3, 2, 4, 3, 4, 4, 6], sources: [20, 6, 0], owner: null, ticket: null, pr: null,
            quote: "Changing the owner on 300 rows one by one is brutal.",
            summary: "{n} asks for selecting many rows and editing a field once. Two come from Enterprise accounts that renew this quarter. No ticket, no owner.",
            samples: [{ who: "@amir", where: "Slack · Pro", text: "Changing the owner on 300 rows one by one is brutal." }, { who: "Brightpath", where: "email · Enterprise", text: "Bulk edit would save our ops team hours every week." }],
            comments: [{ by: "sam", at: "Tue 17:40", text: "Two Enterprise renewals mention this one." }, { by: "kit", at: "Tue 17:41", text: "Tagged both accounts on the theme. It still needs an owner." }] },
        { id: "spin", name: "Upload spinner never ends", column: "nofix", reports: 5, overnight: 5, daily: [0, 0, 0, 0, 0, 1, 4], sources: [4, 1, 0], owner: null, badge: "new", ticket: null, pr: null,
            quote: "Uploaded a big file and the spinner just keeps going.",
            summary: "New theme, started at 02:00 from {n} overnight reports. Same file sizes and same upload step as Large imports time out, so it's probably the same bug. Merge suggested, waits for you.",
            samples: [{ who: "@dina", where: "Slack · Free", text: "Uploaded a big file and the spinner just keeps going." }],
            comments: [{ by: "kit", at: "02:01", text: "Started this theme from 5 overnight reports. It looks like the timeouts, so I suggested a merge." }] },
        { id: "notif", name: "Notifications on mobile", column: "progress", reports: 31, overnight: 1, daily: [2, 3, 3, 4, 4, 7, 8], sources: [9, 2, 20], owner: "dev", ticket: "LIN-244", pr: { number: "#490", state: "In review" },
            quote: "I never know when my import is done on my phone. Looks frozen.",
            summary: "{n} reports. No push when an import finishes, so the app looks frozen on mobile. 11 “app feels stuck” reports moved here this morning, per Dev's rule.",
            samples: [{ who: "@ravi.s", where: "in app · Pro", text: "I never know when my import is done on my phone. Looks frozen." }],
            comments: [{ by: "dev", at: "Tue 18:02", text: "#490 fixes the finish push on iOS. Android next week." }, { by: "kit", at: "09:03", text: "Moved 11 “app feels stuck” reports here. Dev says “stuck” on mobile is the missing push, not a timeout." }] },
        { id: "dates", name: "Dates import as text", column: "merged", reports: 22, overnight: 0, daily: [2, 4, 3, 5, 3, 3, 2], sources: [18, 3, 1], owner: "dev", ticket: "LIN-231", pr: { number: "#482", state: "Merged" },
            quote: "My dates came in as 2026-09-14 text, so sorting is broken.",
            summary: `{n} reports. Dates in day-first locales imported as text. Fixed by PR #482, merged Tue 23:10. {n} retry replies drafted: ${replies.kit} in #feedback threads go via Kit, ${replies.sam} Enterprise go via Sam.`,
            samples: [{ who: "@maria.k", where: "Slack · Pro", text: "My dates came in as 2026-09-14 text, so sorting is broken." }, { who: "Northfield", where: "email · Enterprise", text: "Same date problem as last week. Twice now." }, { who: "@tomasz", where: "Slack · Free", text: "dates = text again" }],
            comments: [{ by: "dev", at: "08:40", text: "@Kit rolling out now, 100% by 3pm" }, { by: "kit", at: "08:41", text: `Holding all ${replies.kit} replies until you hit Confirm live.` }, { by: "sam", at: "10:20", text: "Northfield is in this list. I'll call them before my email goes." }] },
        closed("login", "Login link expired", 31, [6, 5, 4, 2, 1, 1, 0], [6, 25, 0], "dev", "LIN-219", "#466", 12, 14, "The login email link is dead by the time I open it.", "Fixed by #466 (longer link life). {ok} of {asked} people who retried say it works."),
        closed("map", "Column mapping resets", 27, [5, 4, 4, 3, 1, 0, 1], [22, 5, 0], "alex", "LIN-222", "#471", 9, 10, "Every re-upload forgets my column mapping.", "Fixed by #471. {ok} of {asked} confirmed."),
        closed("dup", "Duplicate rows on re-import", 22, [4, 4, 3, 2, 1, 0, 0], [15, 7, 0], "dev", "LIN-225", "#474", 6, 6, "Re-importing doubled every row.", "Fixed by #474. All {asked} retries confirmed."),
        closed("tz", "Wrong timezone in emails", 17, [3, 3, 2, 2, 1, 1, 0], [3, 14, 0], "alex", "LIN-228", "#478", 4, 14, "Digest says 3am, it's 9am here.", "Fixed by #478 on Monday. {ok} of {asked} confirmed so far, I'll ask the rest again Friday."),
    ];
}

const seedActivity: Omit<Activity, "id">[] = [
    { day: "Wednesday", by: "sam", at: "10:20", text: `Sam took the ${replies.sam} Enterprise replies for #482` },
    { day: "Wednesday", by: "dev", at: "09:31", text: "Dev commented on Large imports time out" },
    { day: "Wednesday", by: "kit", at: "09:03", text: "Kit moved 11 “app feels stuck” reports to Notifications on mobile" },
    { day: "Wednesday", by: "you", at: "09:03", text: "You made Large imports time out P0, owned by Dev" },
    { day: "Wednesday", by: "kit", at: "02:01", text: "Kit suggested merging Upload spinner never ends" },
    { day: "Wednesday", by: "kit", at: "02:00", text: "Kit reconciled {overnight} new reports overnight · ran on its own" },
    { day: "Tuesday", by: "kit", at: "23:11", text: "Kit drafted 22 retry replies for #482" },
    { day: "Tuesday", by: "dev", at: "23:10", text: "Dev merged PR #482" },
    { day: "Tuesday", by: "kit", at: "16:19", text: "Kit built this app" },
];

export const DEFAULT_REPLY = "Hey {name}, thanks for flagging the dates issue. It's fixed in PR #482 and live for everyone now. Could you try your import again and tell me here if dates still come in as text?";

/** Story clock: it is Wednesday 10:24, and each action moves it on a minute. */
const START = 10 * 60 + 24;
export function initialFeedback(): Feedback {
    const themes = seedThemes();
    const overnight = String(themes.reduce((sum, theme) => sum + theme.overnight, 0));
    return {
        view: "you", day: "Wednesday", clock: START, live: false, liveAt: null, skipped: false, assigned: null, spinner: null, reply: DEFAULT_REPLY, themes,
        activity: seedActivity.map((entry, index) => ({ ...entry, id: seedActivity.length - index, text: entry.text.replace("{overnight}", overnight) })),
    };
}

export function summarize(themes: Theme[]) {
    const sum = (pick: (theme: Theme) => number) => themes.reduce((total, theme) => total + pick(theme), 0);
    const reports = sum(theme => theme.reports);
    const sources = [0, 1, 2].map(index => sum(theme => theme.sources[index]));
    return {
        reports, themes: themes.length, overnight: sum(theme => theme.overnight),
        loop: { ok: sum(theme => theme.retried?.ok ?? 0), asked: sum(theme => theme.retried?.asked ?? 0) },
        share: sources.map(count => Math.round(count / Math.max(reports, 1) * 100)),
        unowned: themes.filter(theme => !theme.owner),
    };
}

export function describe(theme: Theme) {
    return theme.summary.replaceAll("{n}", String(theme.reports)).replaceAll("{ok}", String(theme.retried?.ok ?? 0)).replaceAll("{asked}", String(theme.retried?.asked ?? 0));
}

export function theme(state: Feedback, id: string) { return state.themes.find(item => item.id === id); }

function stamp(state: Feedback, clock = state.clock + 1): [Feedback, string] {
    const time = String(Math.floor(clock / 60) % 24).padStart(2, "0") + ":" + String(clock % 60).padStart(2, "0");
    return [{ ...state, clock }, time];
}
function log(state: Feedback, by: Author, at: string, text: string): Feedback {
    const id = Math.max(0, ...state.activity.map(entry => entry.id)) + 1;
    return { ...state, activity: [{ id, day: state.day, by, at, text }, ...state.activity] };
}
function patch(state: Feedback, id: string, change: (theme: Theme) => Theme): Feedback {
    return { ...state, themes: state.themes.map(item => item.id === id ? change(item) : item) };
}
/** Comments carry a day only when it isn't the story's first day. */
function commentTime(state: Feedback, time: string) { return state.day === "Wednesday" ? time : state.day.slice(0, 3) + " " + time; }

/** Only Dev, the PR author, confirms; Kit then posts its replies and Sam keeps the Enterprise ones. */
export function confirmLive(state: Feedback): Feedback {
    if (state.live || state.view !== "dev") return state;
    let [next, at] = stamp(state, Math.max(state.clock + 1, 15 * 60 + 40));
    next = { ...next, live: true, liveAt: at };
    next = patch(next, "dates", item => ({ ...item, column: "closed", pr: { number: "#482", state: "Live" }, retried: { ok: 0, asked: item.reports },
        summary: `{n} reports. Dates in day-first locales imported as text. Fixed by PR #482, live since ${at}. ${replies.kit} retry replies posted in #feedback threads; Sam has the ${replies.sam} Enterprise ones.`,
        comments: [...item.comments, { by: "dev", at, text: "Confirmed live for everyone." }, { by: "kit", at, text: `Posted ${replies.kit} retry replies in their threads with the PR link. Sam has the ${replies.sam} Enterprise ones.` }] }));
    next = log(next, "dev", at, "Dev confirmed #482 is live");
    return log(next, "kit", at, `Kit posted ${replies.kit} retry replies in #feedback threads`);
}

/** A day later the retests are in; the one failure becomes its own theme instead of disappearing. */
export function skipAhead(state: Feedback): Feedback {
    if (!state.live || state.skipped) return state;
    let [next, at] = stamp({ ...state, day: "Thursday" }, 11 * 60 + 20);
    const asked = theme(state, "dates")?.reports ?? 0;
    const ok = 11;
    next = { ...next, skipped: true };
    next = patch(next, "dates", item => ({ ...item, retried: { ok, asked }, comments: [...item.comments, { by: "kit", at: "Thu " + at, text: `${ok} of ${asked} say it works. 1 still sees text dates in .xls files, so I reopened that as a new theme for Dev.` }] }));
    next = { ...next, themes: [{ id: "xls", name: "Dates in .xls files", column: "nofix", reports: 1, overnight: 0, daily: [0, 0, 0, 0, 0, 0, 1], sources: [1, 0, 0], owner: "dev", badge: "reopened", ticket: "LIN-258", pr: null,
        quote: "Still getting text dates, but only from my .xls export.",
        summary: "Reopened from #482 after one retry failed. Only .xls files, which go through a separate parser. Assigned to Dev.",
        samples: [{ who: "@tomasz", where: "Slack · Free", text: "Still getting text dates, but only from my .xls export." }],
        comments: [{ by: "kit", at: "Thu " + at, text: "Reopened from #482 and linked the thread. Assigned to Dev." }] }, ...next.themes] };
    next = log(next, "kit", at, "Kit reopened .xls dates as a new theme for Dev");
    return log(next, "kit", at, `Retests for #482: ${ok} of ${asked} say it works`);
}

export function assignBulk(state: Feedback, owner: PersonId): Feedback {
    const bulk = theme(state, "bulk");
    if (!bulk || bulk.owner) return state;
    let [next, at] = stamp(state);
    const name = people[owner].name;
    next = { ...next, assigned: owner };
    next = patch(next, "bulk", item => ({ ...item, owner, ticket: "LIN-257",
        summary: `{n} asks for selecting many rows and editing a field once. Two come from Enterprise accounts that renew this quarter. ${name} owns it; LIN-257 links all {n} reports.`,
        comments: [...item.comments, { by: "you", at: commentTime(next, at), text: `Assigned to ${owner === "you" ? "me" : name}.` }, { by: "kit", at: commentTime(next, at), text: `Told ${owner === "you" ? "you" : name} and created LIN-257 with the ${item.reports} reports linked.` }] }));
    next = log(next, "you", at, `You assigned Bulk edit missing to ${owner === "you" ? "yourself" : name}`);
    return log(next, "kit", at, `Kit created LIN-257 and linked ${bulk.reports} reports`);
}

/** Kit's overnight suggestion: fold the spinner reports into the timeouts. */
export function mergeSpinner(state: Feedback): Feedback {
    const spin = theme(state, "spin");
    if (!spin || state.spinner) return state;
    let [next, at] = stamp(state);
    next = { ...next, spinner: "merge", themes: next.themes.filter(item => item.id !== "spin") };
    next = patch(next, "large", item => ({ ...item, reports: item.reports + spin.reports, overnight: item.overnight + spin.overnight,
        daily: item.daily.map((count, index) => count + (spin.daily[index] ?? 0)),
        sources: item.sources.map((count, index) => count + spin.sources[index]) as Sources,
        samples: [...item.samples, ...spin.samples],
        summary: item.summary + ` ${spin.reports} more merged in from Upload spinner never ends.`,
        comments: [...item.comments, { by: "kit", at: commentTime(next, at), text: `Merged ${spin.reports} reports from Upload spinner never ends, as you approved. Told Dev.` }] }));
    return log(next, "you", at, "You merged Upload spinner never ends into Large imports time out");
}

export function keepSpinner(state: Feedback): Feedback {
    if (!theme(state, "spin") || state.spinner) return state;
    let [next, at] = stamp(state);
    next = { ...next, spinner: "keep" };
    next = patch(next, "spin", item => ({ ...item, badge: undefined, owner: "dev",
        summary: "Started at 02:00 from {n} overnight reports. You kept it separate from Large imports time out; Dev owns it.",
        comments: [...item.comments, { by: "kit", at: commentTime(next, at), text: "Kept separate and assigned to Dev. I'll stop suggesting the merge." }] }));
    return log(next, "you", at, "You kept Upload spinner never ends separate");
}

/** A comment that mentions @Kit gets an acknowledgement; Kit tells the owner when it isn't the commenter. */
export function addComment(state: Feedback, id: string, text: string): Feedback {
    const item = theme(state, id);
    const body = text.trim();
    if (!item || !body) return state;
    let [next, at] = stamp(state);
    const time = commentTime(next, at);
    const mentioned = /@kit\b/i.test(body);
    const owner = item.owner && item.owner !== state.view ? people[item.owner].name : null;
    next = patch(next, id, current => ({ ...current, comments: [...current.comments, { by: state.view, at: time, text: body },
        ...(mentioned ? [{ by: "kit" as const, at: time, text: `Noted on this theme${owner ? ` and told ${owner}` : ""}.` }] : [])] }));
    return log(next, state.view, at, `${people[state.view].name} commented on ${item.name}`);
}

const authors = new Set<unknown>(["you", "dev", "sam", "alex", "kit"]);
const isTheme = (value: Theme) => value && typeof value.id === "string" && typeof value.name === "string" && columns.some(column => column.id === value.column) &&
    Number.isFinite(value.reports) && Array.isArray(value.daily) && Array.isArray(value.sources) && value.sources.length === 3 &&
    Array.isArray(value.samples) && Array.isArray(value.comments) && value.comments.every(comment => authors.has(comment?.by) && typeof comment.text === "string");
/** Restored state is only trusted when it has the shape this version writes. */
export function isFeedback(value: unknown): value is Feedback {
    const state = value as Feedback;
    return !!state && typeof state === "object" && ["you", "dev", "sam"].includes(state.view) && typeof state.day === "string" && Number.isFinite(state.clock) &&
        typeof state.live === "boolean" && typeof state.skipped === "boolean" && typeof state.reply === "string" &&
        Array.isArray(state.themes) && state.themes.length > 0 && state.themes.every(isTheme) && state.themes.some(item => item.id === "dates") &&
        Array.isArray(state.activity) && state.activity.every(entry => authors.has(entry?.by) && Number.isFinite(entry.id) && typeof entry.text === "string");
}
