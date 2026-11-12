import type { ApprovalDetails } from "@web/thread/approval";

/** Which approvals are held down for a second rather than tapped: the ones
 *  that do something a person cannot take back. Money moving, words going
 *  out to someone outside the Lemma, something deleted.
 *
 *  Read from the approval itself, never from the screen around it: the tool
 *  that will run (`tool_name`), and what it will be run with (a connector's
 *  operation, a shell command, a record's action, a query), falling back to
 *  the agent's own title only when the call names nothing more specific. An
 *  operation that only reads is never held, whatever its title says: "Look
 *  up Northwind's refunds" moves no money.
 *
 *  Pure, so the tests read it directly. */

export type Consequence = "money" | "delete" | "outside";

const MONEY = new Set([
    "pay", "pays", "payment", "payments", "payout", "payouts", "charge", "charges", "refund", "refunds",
    "invoice", "invoices", "transfer", "transfers", "purchase", "purchases", "buy", "checkout", "capture",
    "subscription", "subscriptions", "billing", "wire", "withdraw", "withdrawal", "deposit", "amount",
]);

/** Who handles money, so "create" on one of them is money too. */
const PAYMENTS = new Set(["stripe", "paypal", "razorpay", "braintree", "adyen", "square", "wise", "paddle", "chargebee", "dodo", "mollie"]);

const DELETE = new Set(["delete", "deletes", "remove", "destroy", "drop", "purge", "truncate", "wipe", "erase", "unlink", "trash", "rm", "rmdir"]);

const OUTSIDE = new Set(["send", "sends", "reply", "forward", "post", "publish", "tweet", "share", "invite", "broadcast", "email", "mail", "sms", "dm"]);

/** Doing something: one of these on a payments connector is money even
 *  when no word above says so. */
const WRITES = new Set(["create", "update", "cancel", "issue", "make", "add", "set", "submit", "confirm", "execute", "run", "write", "edit"]);

const READS = new Set(["get", "list", "search", "find", "fetch", "read", "retrieve", "describe", "query", "view", "lookup", "look", "count", "check", "show", "preview", "download", "watch"]);

/** Money words that are things done, not things named. */
const MONEY_VERBS = new Set(["pay", "refund", "charge", "transfer", "purchase", "buy", "capture", "withdraw", "deposit", "invoice"]);

/** Lemma's own tools that only reach people inside the Lemma, or this chat. */
const INSIDE = new Set(["channel_post", "say", "ask_a_person", "ask_teammate", "answer_question"]);

/** Tools that run whatever they are handed: the title has to say what. */
const RUNNERS = new Set(["exec_command", "execute_python", "run_connector_operation", "manage_process"]);

/** Words of a name or a sentence: `GMAIL_SEND_EMAIL`, `sendEmail`, `rm -rf`. */
export function wordsOf(text: string | undefined | null): string[] {
    if (!text) return [];
    return text
        .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
        .toLowerCase()
        .split(/[^a-z0-9]+/)
        .filter(Boolean);
}

function param(details: Pick<ApprovalDetails, "params">, ...names: string[]): string {
    const wanted = names.map((name) => name.toLowerCase());
    return details.params.find((one) => wanted.includes(one.name.toLowerCase()))?.value ?? "";
}

function weigh(words: readonly string[]): Consequence | null {
    const has = (set: ReadonlySet<string>) => words.some((word) => set.has(word));
    if (has(MONEY) || (has(PAYMENTS) && has(WRITES))) return "money";
    if (has(DELETE)) return "delete";
    /* A draft is written, not sent. */
    if (has(OUTSIDE) && !(words.includes("draft") && !words.includes("send"))) return "outside";
    return null;
}

/** An operation named verb first (`stripe_list_charges`, `wordpress_get_post`)
 *  whose verb only reads. */
export function onlyReads(words: readonly string[]): boolean {
    const verb = words.find((word) => READS.has(word) || WRITES.has(word) || DELETE.has(word) || OUTSIDE.has(word) || MONEY_VERBS.has(word));
    return verb !== undefined && READS.has(verb);
}

export function consequenceOf(details: Pick<ApprovalDetails, "toolName" | "title" | "params">): Consequence | null {
    const tool = details.toolName ?? "";
    if (INSIDE.has(tool)) return null;
    const operation = wordsOf(param(details, "operation"));
    const command = wordsOf(param(details, "cmd", "command"));
    const action = wordsOf(param(details, "action"));
    const sql = wordsOf(param(details, "sql"));
    /* A connector's operation is what runs; the tool is only the envelope. */
    const acting = operation.length > 0 ? operation : RUNNERS.has(tool) ? [] : wordsOf(tool);
    const said = [...command, ...action, ...sql];
    if (said.length === 0 && acting.length > 0 && onlyReads(acting)) return null;

    const argNames = details.params.flatMap((one) => wordsOf(one.name));
    const found = weigh([...acting, ...said, ...argNames]);
    if (found) return found;
    /* A runner with nothing more to go on: the agent's title says what it is
       for. A specific tool that weighed nothing is taken at its word. */
    return acting.length === 0 && said.length === 0 ? weigh(wordsOf(details.title)) : null;
}

/** What the hold says it is for, beside the button. */
export function consequenceLine(consequence: Consequence): string {
    switch (consequence) {
        case "money":
            return "This moves money";
        case "delete":
            return "This deletes for good";
        case "outside":
            return "This goes outside the Lemma";
    }
}
