import { withReferences } from "../../thread/attachments";
import type { RawMessage, RuntimeNote, Streaming } from "@web/thread/turns";
import { SAMPLE_ORDER, SAMPLE_PHOTOS, SAMPLE_SHEET, SAMPLE_VOICEMAIL } from "./sample-paths";

/** The sample chats, one for each state a thread can be in, so every card
 *  and every line can be looked at without a server. Their ids are the
 *  sample Lemmas' chats in `data/sample.ts`; a chat not listed here falls
 *  back to that file's short transcript. Shaped like a real Lemma's, never a
 *  real one's. */

export interface SampleChat {
    title: string | null;
    status: string;
    messages: RawMessage[];
    /** A page older than `messages`, for "Earlier". */
    older: RawMessage[];
    /** The conversation record's other fields: metadata, a run's failure. */
    conversation: Record<string, unknown>;
    streaming?: Streaming;
    notes?: RuntimeNote[];
    /** What the Lemma says once a pause is answered, by its call id. */
    after?: Record<string, string>;
    /** What it says when a failed run is tried again. */
    retried?: string;
    /** A run played out as the chat opens: its steps one by one, then its
     *  reply streaming in. */
    run?: SampleRun;
}

/** A run the sample engine plays, then the one after it, if any. */
export interface SampleRun {
    steps: { tool: string; args: Record<string, unknown>; result?: unknown; ms: number }[];
    reply: string;
    then?: SampleRun;
}

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

/** A transcript written from a start time, in steps of seconds. */
function writer(start: number) {
    let sequence = 0;
    const at = (seconds: number) => new Date(start + seconds * 1000).toISOString();
    const out: RawMessage[] = [];
    const push = (message: RawMessage) => {
        sequence += 1;
        out.push({ ...message, sequence });
    };
    return {
        out,
        user: (id: string, text: string, seconds: number, metadata?: Record<string, unknown>) =>
            push({ id, role: "user", kind: "TEXT", content: text, created_at: at(seconds), ...(metadata ? { metadata } : {}) }),
        say: (id: string, text: string, seconds: number) => push({ id, role: "assistant", kind: "TEXT", content: text, created_at: at(seconds) }),
        think: (id: string, text: string, seconds: number) => push({ id, role: "assistant", kind: "THINKING", content: text, created_at: at(seconds) }),
        notice: (id: string, text: string, seconds: number) => push({ id, role: "system", kind: "NOTIFICATION", content: text, created_at: at(seconds) }),
        call: (id: string, tool: string, args: Record<string, unknown>, seconds: number, result?: unknown) => {
            push({ id, role: "assistant", kind: "TOOL_CALL", tool_name: tool, tool_args: args, tool_call_id: "call-" + id, created_at: at(seconds) });
            if (result !== undefined) {
                push({ id: id + "-r", role: "tool", kind: "TOOL_RETURN", tool_name: tool, tool_call_id: "call-" + id, tool_result: result, created_at: at(seconds + 3) });
            }
        },
    };
}

const WIDGET = [
    "<style>body{margin:0;font-family:var(--lemma-widget-font);color:var(--lemma-widget-text);background:var(--lemma-widget-surface)}",
    ".row{display:flex;gap:8px;padding:12px}.n{flex:1;border:1px solid var(--lemma-widget-border);border-radius:12px;padding:10px}",
    ".v{font-size:24px}.l{font-size:12px;color:var(--lemma-widget-muted)}</style>",
    "<div class=row><div class=n><div class=v>40</div><div class=l>Answered</div></div>",
    "<div class=n><div class=v>42</div><div class=l>Asked</div></div>",
    "<div class=n><div class=v>2</div><div class=l>Waiting</div></div></div>",
].join("");

/** Support: last week's numbers yesterday, then a refund waiting on a yes. */
function supportRefund(now: number): SampleChat {
    const yesterday = writer(now - DAY - 2 * HOUR);
    yesterday.user("y1", "How did last week go? And what does our refund policy say about broken items?", 0);
    yesterday.call("y2", "web_search", { query: "refund policy damaged in transit UK consumer rights", comment: "Checking what the law asks of us" }, 4, {
        results: [
            { title: "Consumer Rights Act 2015: goods that arrive damaged", url: "https://www.gov.uk/consumer-protection-rights", snippet: "Goods must be of satisfactory quality when they arrive.", publisher: "GOV.UK" },
            { title: "Refunds for faulty goods", url: "https://www.citizensadvice.org.uk/consumer/things-you-have-bought/", snippet: "You can get a full refund within 30 days.", publisher: "Citizens Advice" },
            { title: "Damaged in transit: who pays?", url: "https://www.which.co.uk/consumer-rights/advice", snippet: "The seller is responsible until the goods reach you." },
        ],
    });
    yesterday.call("y3", "read_file", { file_path: "/policies/refunds.md" }, 9, { content: "# Refunds\n\nA customer whose order arrived damaged gets a refund for the damaged items.\n- Up to $500 without a second look.\n- Above that, a person approves it.\n" });
    yesterday.call("y4", "display_resource", { type: "WIDGET", name: "Last week", content: WIDGET, fallback: "Answered 40 of 42; 2 waiting." }, 14, { ok: true });
    yesterday.call("y5", "display_resource", { type: "FILE", path: "/policies/refunds.md" }, 15, { ok: true });
    yesterday.say("y6", "Last week went well: **40 of 42** answered, 2 waiting on a person. Our policy refunds damaged items up to $500 without a second look, which matches what the law asks.", 18);
    yesterday.call("y7", "pod_write_file", { path: "/memory/refunds.md", content: "Refunds: damaged items up to $500, no second look." }, 20, { success: true, path: "/memory/refunds.md" });

    const today = writer(now - 15 * MIN);
    today.user("u1", withReferences("Northwind says their order arrived broken. Photos attached. What do we do?", SAMPLE_PHOTOS), 0);
    today.think("t0", "Four lamps on the order, one cracked. The policy covers it, under $500.", 3);
    today.call("t1", "view_image", { pod_file_path: "/inbox/northwind/lamp.jpg", instructions: "Is the lamp cracked, and is it the shade or the base?" }, 6, { success: true, source: "datastore", file_path: "/inbox/northwind/lamp.jpg", message: "The glass shade is cracked from the rim down; the base is fine.", size_bytes: 284_000 });
    today.call("t2", "display_resource", { type: "TABLE", name: "tickets" }, 12, { ok: true });
    today.call("t4", "display_resource", { type: "FILE", path: SAMPLE_ORDER }, 13, { ok: true });
    today.call("t5", "display_resource", { type: "FILE", path: SAMPLE_SHEET }, 14, { ok: true });
    today.call("t6", "display_resource", { type: "FILE", path: SAMPLE_VOICEMAIL }, 15, { ok: true });
    today.say("a1", "Their order had 4 lamps and one cracked in transit, which our policy covers. Maria left a voicemail too. I drafted the reply to Maria and a refund for the broken lamp.", 20);
    today.call("t3", "request_approval", { title: "Refund $240 to Northwind", reason: "Stripe · order 4471 · 1 of 4 items", tool_name: "stripe_refund", args: { amount: "$240", order: "4471", reason: "damaged in transit" } }, 24);
    return {
        title: "Refund for a broken order",
        status: "WAITING",
        messages: [...yesterday.out, ...today.out.map((message) => ({ ...message, sequence: (message.sequence ?? 0) + 100 }))],
        older: [],
        conversation: {},
        after: { "call-t3": "Refunded. Maria hears back within the hour." },
    };
}

/** Support, three days ago: a long piece of work with one of every step. */
function supportTone(now: number): SampleChat {
    const w = writer(now - 3 * DAY);
    w.user("u1", "Rewrite the apology emails so they sound less like a form letter, and check them on the live help page.", 0);
    w.call("s1", "exec_command", { cmd: "grep -rl \"We apologise for any inconvenience\" templates/", comment: "Finding every template that says it" }, 4, { stdout: "templates/late.md\ntemplates/broken.md\ntemplates/missing.md\n", exit_code: 0 });
    w.call("s2", "edit_file", { file_path: "templates/broken.md", old_string: "We apologise for any inconvenience caused.", new_string: "I’m sorry your lamp arrived broken. A new one is on its way today." }, 10, { success: true });
    w.call("s3", "execute_python", { code: "import statistics\nstatistics.mean([41, 38, 52, 47])", comment: "Average length of the new openings" }, 16, { stdout: "", result: "44.5" });
    w.call("s4", "browser_open", { url: "https://help.northwind-lamps.example/returns", comment: "Opening the live help page" }, 22, { success: true, url: "https://help.northwind-lamps.example/returns", title: "Returns and refunds" });
    w.call("s5", "browser_act", { action: "click", target: "the “Damaged items” tab" }, 26, { success: true, url: "https://help.northwind-lamps.example/returns#damaged" });
    w.call("s6", "browser_screenshot", { full_page: false, instructions: "Does the new wording fit on one screen?" }, 30, { success: true, url: "https://help.northwind-lamps.example/returns#damaged", size_bytes: 412_000 });
    w.call("s7", "web_fetch", { urls: ["https://www.plainenglish.co.uk/how-to-write-in-plain-english.html"], prompt: "What makes an apology read as sincere?" }, 36, {
        pages: [{ url: "https://www.plainenglish.co.uk/how-to-write-in-plain-english.html", title: "How to write in plain English", preview: "Use everyday words. Say sorry once, then say what happens next.", files: { markdown: "/me/fetched/plain-english.md" } }],
    });
    w.call("s8", "task", { description: "Check the other two templates", subagent_type: "writer", prompt: "Rewrite late.md and missing.md in the same voice." }, 42, { output: "Both rewritten. late.md now opens with the new delivery date." });
    w.call("s9", "ask_teammate", { teammate: "Launch", request: "Does the new apology wording clash with the launch copy?" }, 60, JSON.stringify({ teammate_name: "Launch", status: "ANSWERED", answer: "No clash. Keep “on its way today”, the launch copy uses the same phrase.", asked_as: "AS_PERSON", teammate: "sample-launch", conversation_id: "launch-copy" }));
    w.call("s10", "run_connector_operation", { connector: "gmail", operation: "create_draft", payload: { to: "maria@northwind.example", subject: "Your lamp" } }, 70, { result: { id: "draft-81", thread: "t-77" } });
    w.call("s11", "wait_for", { reason: "Waiting for Maria to open the draft", seconds: 600 }, 74, { woke_because: "TIMER", waited_seconds: 600 });
    w.call("s12", "browser_sign_in", { origin: "https://dashboard.stripe.com", reason: "To check the refund went through" }, 80, { outcome: "signed_in", saved: true });
    w.say("a1", "Done. The three templates now say sorry once and then what happens next. The help page fits on one screen, and Launch says the wording matches theirs.\n\n```text\nI’m sorry your lamp arrived broken. A new one is on its way today.\n```", 90);
    w.call("r1", "display_resource", { type: "APP", name: "returns-desk" }, 92, { ok: true });
    w.call("r2", "display_resource", { type: "BROWSER", name: "Help page" }, 93, { url: "https://help.northwind-lamps.example/returns", expires_at: new Date(now + 30 * DAY).toISOString() });
    w.call("r3", "display_resource", { type: "WORKFLOW", name: "chase_unanswered_tickets" }, 94, { ok: true });
    w.notice("n1", "**Morning digest** ran: 3 tickets need a person today.", 3 * 3600);

    const d = writer(now - 2 * DAY);
    d.user("u2", "Here is what Launch said about the sale banner.", 0, { source: "pod_ask_answer", asked_pod_name: "Launch", asked_pod_id: "sample-launch", ask_conversation_id: "launch-copy", ask_mode: "AS_PERSON", ask_outcome: "ANSWERED" });
    d.user("u3", "Keep the tone notes to yourself, don’t post them in the group.", 30, { private_note: true });
    d.say("a2", "Got it. Here is the sale banner as a table:\n\n| Line | Before | After |\n| --- | --- | --- |\n| Headline | Big savings! | Lamps for less, this week |\n| Button | Shop now | See the lamps |", 40);
    return {
        title: "Tone for the apology emails",
        status: "IDLE",
        messages: [...w.out, ...d.out.map((message) => ({ ...message, sequence: (message.sequence ?? 0) + 100 }))],
        older: [],
        conversation: {},
        notes: [{ text: "The model this chat used is no longer offered; it answered on Sonnet 5.5 instead.", turnId: "u1" }],
    };
}

/** Launch, working right now, with a message it has not heard yet. */
function launchCopy(now: number): SampleChat {
    const w = writer(now - 8 * MIN);
    w.user("u1", "Do a second pass on the landing copy. Shorter, and keep the price.", 0);
    w.call("s1", "read_file", { file_path: "site/landing.md" }, 4, { content: "# Lamps for every room\n\nHand-blown glass, from $89.\n" });
    w.call("s2", "write_file", { file_path: "site/landing.md", content: "# A lamp for every room\n\nHand-blown glass. From $89.\n" }, 30, { success: true });
    w.user("q1", "Also try a version without the word “hand-blown”.", 60, { during_active_run: true });
    return {
        title: "Landing copy, second pass",
        status: "RUNNING",
        messages: w.out,
        older: [],
        conversation: {},
        run: {
            steps: [
                { tool: "browser_open", args: { url: "https://staging.northwind-lamps.example", comment: "Checking the staging page" }, result: { success: true, url: "https://staging.northwind-lamps.example", title: "Northwind Lamps" }, ms: 2600 },
                { tool: "browser_screenshot", args: { full_page: false, comment: "Seeing if the headline fits on a phone" }, result: { success: true, size_bytes: 318_000 }, ms: 2400 },
                { tool: "edit_file", args: { file_path: "site/landing.md", comment: "Trimming the second line" }, result: { success: true }, ms: 2000 },
            ],
            reply: "Done. The shorter version is on staging:\n\n**A lamp for every room**, then “Hand-blown glass. From $89.” The headline now fits on one line on a phone, and the price stays where it was.",
            then: {
                steps: [{ tool: "edit_file", args: { file_path: "site/landing-b.md", comment: "Writing the version without “hand-blown”" }, result: { success: true }, ms: 2200 }],
                reply: "And without “hand-blown”: **A lamp for every room**, then “Glass, shaped by hand. From $89.” It is on staging as a second version, so you can compare the two.",
            },
        },
    };
}

/** Launch: two questions, one of them several answers. */
function launchDay(now: number): SampleChat {
    const w = writer(now - 2 * HOUR);
    w.user("u1", "Plan the launch post.", 0);
    w.say("a1", "The post is written. Two things before I schedule it:", 8);
    w.call("q1", "ask_user", {
        title: "Before I schedule the launch post",
        questions: [
            { question: "Which day should the launch post go out?", header: "day", options: [{ label: "Mon 10", description: "The press list is ready" }, { label: "Tue 11", description: "After the newsletter" }, { label: "Wed 12" }] },
            { question: "Where should it go?", header: "where", multi_select: true, options: [{ label: "The blog" }, { label: "LinkedIn" }, { label: "The newsletter" }] },
        ],
    }, 10);
    return { title: "Monday launch", status: "WAITING", messages: w.out, older: [], conversation: {}, after: { "call-q1": "Scheduled. I moved the press list to match." } };
}

/** Launch: stopped on a site only a person can sign in to. */
function launchPress(now: number): SampleChat {
    const w = writer(now - 2 * DAY);
    w.user("u1", "Follow up with the press list from last week.", 0);
    w.call("s1", "browser_open", { url: "https://app.prtracker.example/lists/spring" }, 5, { success: true, url: "https://app.prtracker.example/login", title: "Sign in" });
    w.call("s2", "browser_sign_in", { origin: "https://app.prtracker.example", reason: "The press list is behind a login" }, 9);
    return { title: "Press list follow-ups", status: "WAITING", messages: w.out, older: [], conversation: {} };
}

/** Renewals: an email thread with a customer, so a message can go two ways. */
function renewalsGlobex(now: number): SampleChat {
    const w = writer(now - DAY - HOUR);
    w.user("u1", "Hi, can we move our renewal to the 1st of next month?", 0, { sender_display_name: "Priya at Globex", surface_platform: "RESEND" });
    w.say("a1", "I can move it. Sam owns this account, so I’ve asked Sam to confirm.", 20);
    w.call("q1", "ask_user", { questions: [{ question: "Sam hasn’t confirmed Globex. It renews in 3 days. Move it to the 1st?", header: "move", options: [{ label: "Move it" }, { label: "Keep the date" }] }] }, 30);
    return {
        title: "Globex renewal",
        status: "WAITING",
        messages: w.out,
        older: [],
        conversation: { metadata: { source: "agent_surfaces", surface_platform: "RESEND", conversation_kind: "EMAIL", channel_name: "Globex", audience: "outsiders" } },
        after: { "call-q1": "Moved. Priya has the new date by email." },
    };
}

/** Hiring: a question to answer in your own words. */
function hiringLee(now: number): SampleChat {
    const w = writer(now - 5 * HOUR);
    w.user("u1", "Set up Lee’s final round.", 0);
    w.call("q1", "ask_user", { questions: [{ question: "Pick a time for Lee’s final round", header: "time", options: [{ label: "Thu 10:00" }, { label: "Thu 14:30" }, { label: "Fri 11:00" }] }] }, 6);
    return { title: "Lee’s final round", status: "WAITING", messages: w.out, older: [], conversation: {}, after: { "call-q1": "Booked. Lee and the panel have the invite." } };
}

/** Hiring: a run that has said it is working and then nothing. */
function hiringRemote(now: number): SampleChat {
    const w = writer(now - 3 * DAY);
    w.user("u1", "Draft the second role’s job post.", 0);
    w.call("s1", "read_file", { file_path: "roles/second.md" }, 3, { content: "Senior support lead" });
    return { title: "The second role", status: "RUNNING", messages: w.out, older: [], conversation: {} };
}

/** Hiring: a run that failed, and can be tried again. */
function hiringOld(now: number): SampleChat {
    const w = writer(now - 9 * DAY);
    w.user("u1", "Set up the interview loop for the support lead.", 0);
    w.call("s1", "run_connector_operation", { connector: "google-calendar", operation: "create_event", payload: { title: "Interview loop" } }, 4, { error: "auth_expired", code: "REAUTH_REQUIRED", message: "The Google Calendar account needs connecting again." });
    return {
        title: "Interview loop",
        status: "FAILED",
        messages: w.out,
        older: [],
        conversation: { last_run_error: "The calendar refused the invite: the account needs connecting again.", last_run_retryable: true },
        retried: "The calendar took it this time. The loop is on everyone’s calendar.",
    };
}

/** Books: a long chat with an earlier page, and no model to think with. */
function booksClose(now: number): SampleChat {
    const early = writer(now - 12 * DAY);
    for (let index = 0; index < 6; index += 1) {
        early.user("e" + index, ["Start the September close.", "Which invoices are still open?", "Chase Initech.", "Did Umbrella pay?", "Book the bank fees.", "Send me the summary."][index], index * 600);
        early.say("f" + index, ["Started. Bank feeds are in.", "Two: Initech $1,180 and Umbrella $420.", "Chased, with the invoice attached.", "Not yet. I’ll look again tomorrow.", "Booked to 7900, bank charges.", "Sent. Everything reconciles except Umbrella."][index], index * 600 + 30);
    }
    const late = writer(now - 8 * DAY);
    late.user("u1", "Close the month.", 0);
    return {
        title: "September close",
        status: "FAILED",
        messages: late.out.map((message) => ({ ...message, sequence: (message.sequence ?? 0) + 100 })),
        older: early.out,
        conversation: { last_run_error: "No AI model is set up for this organization.", last_run_error_code: "model_not_configured", last_run_retryable: false },
    };
}

const CHATS: Record<string, (now: number) => SampleChat> = {
    "support-refund": supportRefund,
    "support-tone": supportTone,
    "launch-copy": launchCopy,
    "launch-day": launchDay,
    "launch-press": launchPress,
    "renewals-globex": renewalsGlobex,
    "hiring-lee": hiringLee,
    "hiring-remote": hiringRemote,
    "hiring-old": hiringOld,
    "books-close": booksClose,
};

export function sampleChatOf(id: string, now: number): SampleChat | null {
    return CHATS[id]?.(now) ?? null;
}
