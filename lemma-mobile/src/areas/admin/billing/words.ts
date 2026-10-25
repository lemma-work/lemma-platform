import type { MyUsageLimitsResponse, UsageAllowanceResponse } from "lemma-sdk";
import { count } from "../words";
import { canManage, roleLabel } from "@web/org/membership";
import type { ContactsCap, DaySpend } from "./source";
import { formatCost, resetsIn } from "@web/usage/allowance";
import { isContactSales, isPerSeat, isTrouble, onDate, paying, standing, type Invoice, type Plan, type SeatInfo, type StartedCheckout, type Subscription } from "@web/billing/plan";

/** What the usage and billing screens decide and say. Pure, so `tests/` can
 *  hold it: nothing here imports `react-native`; the screens pass in what
 *  they know (the phone's OS, the server's web address, a role). */

/** How far back spending is read, in days: the web's three. */
export const RANGES = [7, 30, 90] as const;
export type Range = (typeof RANGES)[number];

/* ── buying, and where it is not offered ─────────────────────────────── */

/** Whether this phone offers buying, and what it says where it does not. */
export interface Buying {
    /** Every purchase, upgrade, checkout, seat, payment and "stop renewing"
     *  control is drawn only when this is true. */
    offer: boolean;
    /** The one quiet sentence said in their place, or null where they are. */
    elsewhere: string | null;
}

/** The rule for paying from the phone. Apple does not let an iOS app link
 *  out to buy or manage a subscription, so on iOS the plan and the billing
 *  are shown and nothing that pays or changes paying is: one sentence says
 *  where to do it instead, naming the server's own web address (never a
 *  fixed host, so another deployment names itself). Android offers it all,
 *  through the phone's browser. */
export function buyingHere(os: string, web: string, whose: "mine" | "organization"): Buying {
    if (os !== "ios") return { offer: true, elsewhere: null };
    const at = hostOf(web);
    return {
        offer: false,
        elsewhere: whose === "mine"
            ? `Change your plan on the web, at ${at}.`
            : `Change what this organization pays for on the web, at ${at}.`,
    };
}

/** "lemma.work" from "https://lemma.work/". */
export function hostOf(web: string): string {
    return web.trim().replace(/^[a-z]+:\/\//i, "").replace(/\/+$/, "");
}

/** How often a checkout is asked about, by how long it has been watched (the
 *  web's poll): every three seconds for two minutes, when a payment that went
 *  through lands; every fifteen for somebody who stopped to find a card; then
 *  not at all after a quarter of an hour. Coming back to the app asks once more. */
export function checkoutPollMs(watchedMs: number): number | false {
    if (watchedMs < 2 * 60_000) return 3_000;
    if (watchedMs < 15 * 60_000) return 15_000;
    return false;
}

/** What starting a plan came to: a checkout to open, a plan already moved
 *  (nothing to pay, or sample data), or nothing usable. */
export type Started =
    | { kind: "open"; url: string }
    | { kind: "settled" }
    | { kind: "failed"; said: string };

export function afterStart(started: StartedCheckout): Started {
    const url = started.checkout_url?.trim();
    if (url) return { kind: "open", url };
    if (started.subscription) return { kind: "settled" };
    /* No address and nothing written would be a button that looked live and
       did nothing. */
    return { kind: "failed", said: "That plan could not be started. Nothing has been charged." };
}

/* ── allowance ───────────────────────────────────────────────────────── */

/** How a window reads: quiet, near its cap (the server's own threshold), or
 *  spent, which refuses the next run whatever the others say. */
export function windowTone(window: UsageAllowanceResponse, limits: Pick<MyUsageLimitsResponse, "warning_percent"> | null | undefined): "quiet" | "warn" | "blocked" {
    if (!window.allowed) return "blocked";
    const warn = typeof limits?.warning_percent === "number" && limits.warning_percent > 0 && limits.warning_percent <= 100 ? limits.warning_percent : 80;
    return window.used_percent >= warn ? "warn" : "quiet";
}

/** "Resets in 3 days", or "Spent. Resets in 3 days" for a window used up. */
export function windowNote(window: UsageAllowanceResponse, now = new Date()): string {
    const resets = resetsIn(window.reset_at, now);
    const said = resets ? resets[0].toUpperCase() + resets.slice(1) : "";
    if (window.allowed) return said;
    return said ? `Spent. ${said}` : "Spent";
}

/** A meter's fill: capped at full, since past the cap the number keeps
 *  climbing and a bar that kept pace would leave the screen. */
export function meterFill(usedPercent: number): number {
    if (!Number.isFinite(usedPercent)) return 0;
    return Math.min(1, Math.max(0, usedPercent / 100));
}

/** Where raising the allowance is decided: the organization's billing when
 *  it pays, else your own plan. */
export function planPlace(limits: Pick<MyUsageLimitsResponse, "plan_type"> | null | undefined): "plan" | "team-billing" {
    return limits?.plan_type === "TEAM" ? "team-billing" : "plan";
}

/* ── spending ────────────────────────────────────────────────────────── */

const KINDS: Record<string, string> = { LLM: "Language models", IMAGE: "Images", AUDIO: "Audio", EMBEDDING: "Embeddings" };

/** A kind of work, in words: "Language models", not "LLM". */
export function kindLabel(kind: string): string {
    const known = KINDS[kind.trim().toUpperCase()];
    if (known) return known;
    const tidy = kind.trim().replace(/[_-]+/g, " ").toLowerCase();
    return tidy ? tidy[0].toUpperCase() + tidy.slice(1) : "Other";
}

/** "1,204,332". */
export function tokens(n: number): string {
    return Number.isFinite(n) ? Math.round(n).toLocaleString("en-US") : "0";
}

/** One bar per day, each a fraction of the busiest day's height. A quiet day
 *  keeps a sliver rather than vanishing, so a week's shape reads true. No
 *  bars at all when nothing was spent. */
export function dayBars(days: readonly DaySpend[]): { heights: number[]; busiest: DaySpend | null } {
    const busiest = days.reduce<DaySpend | null>((high, day) => (day.cost > (high?.cost ?? 0) ? day : high), null);
    if (!busiest) return { heights: [], busiest: null };
    return { heights: days.map((day) => Math.max(0.02, Math.min(1, day.cost / busiest.cost))), busiest };
}

/** "Busiest day: 14 October, $1.20". */
export function busiestWords(busiest: DaySpend): string {
    const day = onDate(busiest.day);
    return `Busiest day: ${day ? day + ", " : ""}${formatCost(busiest.cost)}`;
}

/* ── the contacts cap ────────────────────────────────────────────────── */

function dollars(value: number): string {
    return "$" + (value < 10 ? value.toFixed(2) : Math.round(value).toString());
}

/** "$3.20 of $25 this month", or "$3.20 this month, no cap set". */
export function capWords(cap: ContactsCap): string {
    const spent = dollars(cap.spent);
    return cap.limit == null ? `${spent} this month, no cap set` : `${spent} of ${dollars(cap.limit)} this month`;
}

/** What was typed as a cap: dollars, or nothing for no cap. */
export function readCapTyped(typed: string): { limit: number | null } | { problem: string } {
    const said = typed.trim().replace(/^\$\s*/, "").replace(/,/g, "");
    if (!said) return { limit: null };
    const amount = Number(said);
    if (!Number.isFinite(amount) || amount < 0) return { problem: "Enter an amount in dollars, or leave it empty for no cap." };
    return { limit: amount };
}

/* ── what is paid for ────────────────────────────────────────────────── */

/** Money in cents, printed: whole dollars without ".00", cents where a price
 *  has them (the web's `formatPrice`). Written out with both fraction bounds
 *  because an engine on the older Intl rules refuses a maximum of 0 under
 *  the currency's default minimum of 2, and the price would throw instead of
 *  print. `tests/` holds it equal to the web's. */
export function priceOf(cents: number, currency = "USD"): string {
    const digits = cents % 100 === 0 ? 0 : 2;
    return new Intl.NumberFormat("en-US", { style: "currency", currency, minimumFractionDigits: digits, maximumFractionDigits: digits }).format(cents / 100);
}

/** A plan's price with its unit, which is how it has to be read to be true
 *  (the web's `priceLine`, over `priceOf`). */
export function priceWords(plan: Plan): string {
    if (isContactSales(plan)) return "Talk to us";
    if (plan.price_cents === 0) return "Free";
    const money = priceOf(plan.price_cents, plan.currency);
    return isPerSeat(plan) ? money + " per seat / month" : money + " / month";
}

/** The plan a subscription holds, in the words the screen draws. Free and no
 *  subscription at all are one sentence (`none`), because to the person
 *  reading they are one situation: nothing is charged. */
export function currentWords(subscription: Subscription | null | undefined, none: string): {
    name: string;
    price: string;
    renews: string | null;
    note: string | null;
    trouble: boolean;
} {
    const plan = subscription?.plan ?? null;
    const paid = paying(subscription);
    const ends = onDate(subscription?.current_period_end);
    return {
        name: plan?.name ?? "No plan yet",
        price: paid && plan ? priceWords(plan) : none,
        renews: paid && ends && !subscription?.cancel_at_period_end ? `Renews ${ends}` : null,
        note: standing(subscription) || null,
        trouble: isTrouble(subscription),
    };
}

/** Seats paid for and people present, which differ in the case that
 *  matters: a team that grew mid-period has more people than seats, and
 *  nothing charges for them until the plan next changes. */
export function seatWords(seats: Pick<SeatInfo, "current_seats" | "member_count">, paid: boolean): { bought: string; present: string; short: string | null } {
    const bought = seats.current_seats ?? 0;
    const present = seats.member_count ?? 0;
    return {
        bought: count(bought, "seat", "seats"),
        present: count(present, "person", "people"),
        short: paid && present > bought ? `${count(present - bought, "person", "people")} not yet paid for.` : null,
    };
}

export function invoiceWord(status: Invoice["status"] | string): string {
    if (status === "paid") return "Paid";
    if (status === "unpaid") return "Due";
    if (status === "failed") return "Payment failed";
    if (status === "void") return "Void";
    return "Draft";
}

/** "1 September – 30 September". */
export function invoicePeriod(invoice: Pick<Invoice, "period_start" | "period_end">): string {
    return [onDate(invoice.period_start), onDate(invoice.period_end)].filter(Boolean).join(" – ") || "Invoice";
}

/** What somebody may do to the organization's billing, by their role there
 *  (the platform's three permissions): anyone in it reads the plan and the
 *  seats; an owner or an editor buys, switches and reads the invoices; only
 *  an owner stops it. `held` says why a member is offered no buying, once
 *  their role is known. */
export function teamPowers(role: string | null | undefined, answered: boolean): {
    mayBuy: boolean;
    mayStop: boolean;
    seesInvoices: boolean;
    held: string | null;
} {
    const mayBuy = canManage(role);
    return {
        mayBuy,
        mayStop: role === "ORG_OWNER",
        seesInvoices: mayBuy,
        held: answered && !mayBuy ? "An owner or an editor buys and changes the plan for this organization." : null,
    };
}

/** Who may change the organization's billing, and what the reader is. */
export function whoMayChange(role: string | null | undefined): string {
    const rule = "Anyone here can see the plan and its seats. Owners and editors choose and change the plan; only an owner stops it.";
    if (!role) return rule;
    const what = roleLabel(role).toLowerCase();
    return `${rule} You are ${/^[aeiou]/.test(what) ? "an" : "a"} ${what} here.`;
}

/** Whether this is an installation on somebody's own computer, which has no
 *  plans, seats or invoices. */
export function isLocal(deployment: string | null): boolean {
    return deployment === "local";
}
