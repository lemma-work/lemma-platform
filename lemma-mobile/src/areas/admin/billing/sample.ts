import type { MyUsageLimitsResponse, UsageAllowanceResponse } from "lemma-sdk";
import { SAMPLE_MEMBERS } from "../access/sample";
import type { BillingSource, ContactsCap, DaySpend, Payer, Spend, SpendScope } from "./source";
import type { Slice } from "@web/usage/allowance";
import { isContactSales, type Invoice, type Plan, type Subscription } from "@web/billing/plan";

/** Acme's usage and billing: Ada works inside Acme, which pays for the Team
 *  plan (three seats for four people, so the gap shows), and is herself on
 *  Free. Every control can be tried: choosing a plan moves it at once (there
 *  is no payment to wait for), switching and stopping renewal change it in
 *  memory until the app closes. Shaped like a real organization, never a
 *  real one. */

const DAY = 24 * 60 * 60_000;
const ACME = "sample-acme";

const iso = (ms: number) => new Date(ms).toISOString();
const wait = (ms: number) => new Promise<void>((done) => setTimeout(done, ms));

function plan(id: string, name: string, description: string, priceCents: number, audience: "PERSONAL" | "TEAM", features: Record<string, unknown>): Plan {
    return {
        id: `sample-plan-${id}`,
        name,
        description,
        plan_type: audience,
        price_cents: priceCents,
        currency: "USD",
        features,
        seat_limit: audience === "PERSONAL" ? 1 : null,
        usage_limits: {},
        is_active: true,
    };
}

const monthly = (seat: boolean, highlights: string[]) => ({
    billing_mode: "fixed_subscription",
    billing_interval: "MONTHLY",
    highlights,
    ...(seat ? { price_unit: "seat" } : {}),
});

const PERSONAL = ["Usage shown as a share of your allowance", "Your own private Lemmas"];
const TEAM = ["Usage shown as a share of your allowance", "Lemmas the whole team shares", "Your own branding, not ours"];

const PLANS: Plan[] = [
    plan("free", "Free", "Where everybody starts.", 0, "PERSONAL", { billing_mode: "fixed_subscription" }),
    plan("personal", "Lemma Personal", "For one person. Fair usage for everyday work.", 1000, "PERSONAL",
        monthly(false, ["Fair usage for everyday work", ...PERSONAL])),
    plan("personal-plus", "Lemma Personal Plus", "For one person. Higher usage, for longer and heavier runs.", 2500, "PERSONAL",
        monthly(false, ["Higher limits, around three times Personal", ...PERSONAL])),
    plan("personal-max", "Lemma Personal Max", "For one person. Our highest usage, for continuous heavy work.", 20000, "PERSONAL",
        monthly(false, ["Our highest limits, around ten times Plus", ...PERSONAL])),
    plan("team", "Lemma Team", "For teams. Fair usage for everyday work, paid per seat.", 1000, "TEAM",
        monthly(true, ["Fair usage for everyday work", ...TEAM])),
    plan("team-plus", "Lemma Team Plus", "For teams. Higher usage, for longer and heavier runs. Paid per seat.", 2500, "TEAM",
        monthly(true, ["Higher limits, around three times Team", ...TEAM])),
    plan("team-max", "Lemma Team Max", "For teams. Our highest usage, for continuous heavy work. Paid per seat.", 20000, "TEAM",
        monthly(true, ["Our highest limits, around ten times Plus", ...TEAM])),
    plan("enterprise", "Lemma Enterprise", "For large organizations. A custom allowance, security and support.", 0, "TEAM", {
        billing_mode: "contact_sales",
        price_unit: "custom",
        highlights: ["A custom allowance", "Single sign-on and audit controls", "Custom contracts and dedicated support"],
    }),
];

const byId = (planId: string) => PLANS.find((one) => one.id === planId) ?? null;

const startedAt = Date.now();

/** Ada's own: Free, as most people are. */
let mine: Subscription = {
    id: "sample-sub-ada",
    user_id: "me",
    organization_id: null,
    plan_id: PLANS[0].id,
    plan: PLANS[0],
    status: "active",
    dodo_subscription_id: null,
    current_period_start: null,
    current_period_end: null,
    seat_count: 1,
    cancel_at_period_end: false,
};

/** Acme's: Team, three seats, renewing in nine days. */
let acme: Subscription = {
    id: "sample-sub-acme",
    user_id: null,
    organization_id: ACME,
    plan_id: PLANS[4].id,
    plan: PLANS[4],
    status: "active",
    dodo_subscription_id: "sample-provider-acme",
    current_period_start: iso(startedAt - 21 * DAY),
    current_period_end: iso(startedAt + 9 * DAY),
    seat_count: 3,
    cancel_at_period_end: false,
};

let cap: ContactsCap = { limit: 25, spent: 3.2 };

function invoices(): Invoice[] {
    const start = Date.parse(acme.current_period_start ?? iso(startedAt));
    return [0, 1, 2, 3].map((back) => {
        const from = start - back * 30 * DAY;
        const seats = back >= 3 ? 2 : 3;
        return {
            id: `sample-invoice-${back}`,
            status: back === 0 ? "unpaid" : "paid",
            period_start: iso(from),
            period_end: iso(from + 30 * DAY),
            currency: "USD",
            seat_count: seats,
            total_cents: seats * (acme.plan?.price_cents ?? 1000),
            /* Nothing to pay at in sample data. */
            checkout_url: null,
            paid_at: back === 0 ? null : iso(from + 30 * DAY + DAY),
        };
    });
}

/** A day's spending, steady on weekdays and quiet at weekends. The same
 *  every time it is asked, so a range switched back reads the same. */
function dailyCosts(scale: number, days: number): DaySpend[] {
    const out: DaySpend[] = [];
    const today = new Date();
    today.setHours(12, 0, 0, 0);
    for (let back = days - 1; back >= 0; back -= 1) {
        const at = new Date(today.getTime() - back * DAY);
        const seed = (at.getDate() * 37 + at.getMonth() * 11) % 13;
        const weekend = at.getDay() === 0 || at.getDay() === 6;
        const cost = (weekend ? 0.03 + seed * 0.004 : 0.32 + seed * 0.061) * scale;
        out.push({ day: at.toISOString().slice(0, 10), cost: Math.round(cost * 10_000) / 10_000 });
    }
    return out;
}

const scaleOf = (scope: SpendScope) => (scope.whose === "organization" ? 4.6 : 1);

/** Where the money went, as shares of the total. The image model has no
 *  price yet, so it reports tokens and no cost, as a real one can. */
function slices(total: number, tokens: number, shares: readonly [string, number | null][]): Slice[] {
    return shares.map(([label, share]) => ({
        label,
        cost: share === null ? null : Math.round(total * share * 10_000) / 10_000,
        tokens: Math.round(tokens * (share ?? 0.01)),
    }));
}

function spendOf(scope: SpendScope, days: number): Spend {
    const total = dailyCosts(scaleOf(scope), days).reduce((sum, day) => sum + day.cost, 0);
    const tokens = Math.round(total * 214_000);
    const tokensIn = Math.round(tokens * 0.83);
    return {
        cost: Math.round(total * 10_000) / 10_000,
        tokensIn,
        tokensOut: tokens - tokensIn,
        tokens,
        byModel: slices(total, tokens, [
            ["claude-sonnet-5", 0.61], ["gpt-5-mini", 0.2], ["deepseek-chat", 0.12],
            ["whisper-large-v3", 0.04], ["text-embedding-3-small", 0.03], ["image-preview", null],
        ]),
        byKind: slices(total, tokens, [["LLM", 0.93], ["AUDIO", 0.04], ["EMBEDDING", 0.03], ["IMAGE", null]]),
    };
}

function allowance(key: string, label: string, usedPercent: number, resetsInDays: number): UsageAllowanceResponse {
    return { key, label, used_percent: usedPercent, allowed: usedPercent < 100, reset_at: iso(Date.now() + resetsInDays * DAY) };
}

function limitsOf(orgId: string | null): MyUsageLimitsResponse {
    const windows = orgId
        ? [
            allowance("user_weekly", "Your weekly allowance", 64.2, 3),
            /* Past the warning threshold, so the amber state can be seen. */
            allowance("user_monthly", "Your monthly allowance", 83.5, 9),
            allowance("org_monthly", "Organization monthly allowance", 41.7, 9),
        ]
        : [allowance("user_weekly", "Your weekly allowance", 22.4, 3), allowance("user_monthly", "Your monthly allowance", 9.1, 9)];
    return {
        allowed: windows.every((one) => one.allowed),
        organization_id: orgId,
        plan_name: orgId ? acme.plan?.name ?? null : mine.plan?.name ?? null,
        plan_type: orgId ? "TEAM" : "PERSONAL",
        warning_percent: 80,
        windows,
    };
}

/** Put a payer on a plan at once: sample data has no payment to wait for. */
function moveTo(payer: Payer, planId: string): Subscription {
    const chosen = byId(planId);
    if (!chosen) throw new Error("There is no such plan.");
    if (isContactSales(chosen)) throw new Error("This plan is bought by talking to us.");
    const now = Date.now();
    const moved = {
        plan_id: chosen.id,
        plan: chosen,
        status: "active" as const,
        dodo_subscription_id: payer === null ? "sample-provider-ada" : "sample-provider-acme",
        current_period_start: iso(now),
        current_period_end: iso(now + 30 * DAY),
        cancel_at_period_end: false,
    };
    if (payer === null) mine = { ...mine, ...moved };
    else acme = { ...acme, ...moved };
    return payer === null ? mine : acme;
}

export function billingSample(): BillingSource {
    return {
        limits: async (orgId) => limitsOf(orgId),
        spend: async (scope, days) => spendOf(scope, days),
        daily: async (scope, days) => dailyCosts(scaleOf(scope), days),
        contactsCap: async () => ({ ...cap }),
        setContactsCap: async (_orgId, limit) => {
            await wait(300);
            cap = { ...cap, limit };
            return { ...cap };
        },

        plans: async () => PLANS,
        subscription: async (payer) => (payer === null ? mine : acme),
        seats: async () => ({
            organization_id: ACME,
            has_available_seats: acme.seat_count > SAMPLE_MEMBERS.length,
            seat_limit: null,
            current_seats: acme.seat_count,
            member_count: SAMPLE_MEMBERS.length,
            seats_remaining: null,
        }),
        invoices: async () => invoices(),
        start: async (payer, planId) => {
            await wait(400);
            return { subscription: moveTo(payer, planId), checkout_url: null, message: "Sample data: nothing is charged." };
        },
        change: async (payer, planId) => {
            await wait(400);
            moveTo(payer, planId);
            return { message: "Sample data: nothing is charged." };
        },
        cancel: async (payer) => {
            await wait(300);
            const held = payer === null ? mine : acme;
            const stopped = { ...held, cancel_at_period_end: true };
            if (payer === null) mine = stopped;
            else acme = stopped;
            return { subscription_id: stopped.id, status: stopped.status, message: "It will not renew.", effective_date: stopped.current_period_end };
        },
    };
}
