import type { MyUsageLimitsResponse } from "lemma-sdk";
import type { Slice } from "@web/usage/allowance";
import type { Cancellation, ChangeAcknowledged, Invoice, Plan, SeatInfo, StartedCheckout, Subscription } from "@web/billing/plan";

/** Usage and paying: yours and the organization's (PARITY.md, F).
 *
 *  Everything this part reads or changes. The live side is `live.ts`, the
 *  sample side `sample.ts`; screens reach it through `hooks.ts`, never the
 *  SDK directly, so sample data and a live account are the same screens.
 *
 *  Two kinds of answer, from two places. Usage (`/usage/*`) is metered on
 *  every run whether or not anything caps it, and an allowance is a separate,
 *  optional layer over it. Plans, seats and invoices (`/billing/*`) are what
 *  is paid; the SDK carries neither their calls nor their types, so their
 *  shapes are the web's (`web/plan.ts`), money in whole cents. */

/** Whose spending is read: yours (within an organization when one is given,
 *  else everywhere), or a whole organization's. */
export type SpendScope =
    | { whose: "mine"; orgId: string | null }
    | { whose: "organization"; orgId: string };

/** What was spent over a window, read for a phone: the totals, and where the
 *  money went, largest first. */
export interface Spend {
    /** Dollars. Null when the server could not say, never a guessed zero. */
    cost: number | null;
    tokensIn: number;
    tokensOut: number;
    tokens: number;
    /** By model. A model with no price says so (`cost: null`). */
    byModel: Slice[];
    /** By kind of work: language models, images, audio, embeddings. */
    byKind: Slice[];
}

/** One day's spending: the day (an ISO date) and its dollars. */
export interface DaySpend {
    day: string;
    cost: number;
}

/** What answering people outside the organization may cost it a month, and
 *  what it has this month. Contacts are never billed: this is the ceiling. */
export interface ContactsCap {
    /** Dollars a month, or null for no cap. */
    limit: number | null;
    spent: number;
}

/** Who pays: null for you, else an organization's id. */
export type Payer = string | null;

export interface BillingSource {
    /** Your allowance: the windows that may stop your next run (your week,
     *  your month, the organization's month), and the plan funding them. With
     *  no organization the server answers for your own windows only. */
    limits(orgId: string | null): Promise<MyUsageLimitsResponse>;
    /** Totals over the last `days`, with where the money went. */
    spend(scope: SpendScope, days: number): Promise<Spend>;
    /** One figure per day over the last `days`, oldest first. */
    daily(scope: SpendScope, days: number): Promise<DaySpend[]>;
    /** The organization's cap on what answering contacts may cost a month. */
    contactsCap(orgId: string): Promise<ContactsCap>;
    /** Set that cap in dollars, or null for none. An owner's to change. */
    setContactsCap(orgId: string, limit: number | null): Promise<ContactsCap>;

    /** Every plan on offer, to everybody: the screens pick out their own. */
    plans(): Promise<Plan[]>;
    /** What a payer is on. Null where there is nothing: a new account has no
     *  subscription until its first run writes the free one. */
    subscription(payer: Payer): Promise<Subscription | null>;
    /** Seats an organization has paid for, and the people in it. */
    seats(orgId: string): Promise<SeatInfo>;
    /** An organization's invoices, newest first. Owners and editors only. */
    invoices(orgId: string): Promise<Invoice[]>;
    /** Start paying for a plan: answers the provider's checkout to open, and
     *  changes nothing until the payment lands. */
    start(payer: Payer, planId: string): Promise<StartedCheckout>;
    /** Move between paid plans: the card on file settles the difference, and
     *  the plan moves when the provider says it has. */
    change(payer: Payer, planId: string): Promise<ChangeAcknowledged>;
    /** Stop renewing: access runs to the end of the period paid for. */
    cancel(payer: Payer): Promise<Cancellation>;
}
