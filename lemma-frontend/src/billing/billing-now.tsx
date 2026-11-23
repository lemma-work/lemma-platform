"use client";

import { useState } from "react";
import { ConfirmDialog } from "@/shell/confirm-dialog";
import { formatPercent, resetsIn, type Allowance } from "@/usage/allowance";
import { formatPrice, isPerSeat, monthlyCents, onDate, paying, shortName, type Subscription } from "./plan";

/** What you are on, what it costs, and how close you are to its limit.
 *
 *  The answer to the first question anybody opening Billing has, before the
 *  plans: one name, one monthly figure and when it next renews, and beside it
 *  the usage the plan allows — the reason most people come here at all. The
 *  meter is the plan's case made with the reader's own numbers, so it says
 *  nothing about a plan until usage is near the limit, and then says it once.
 *
 *  Stopping a renewal starts here in red ink and is asked in a dialog, because
 *  nothing on this side resumes a subscription once it is stopped.
 */
export function BillingNow({
    subscription,
    orgName,
    people,
    allowance,
    canBuy,
    stop,
}: {
    subscription: Subscription | null;
    /** The organization's name, for a team plan. */
    orgName?: string;
    /** Everyone in the organization, for a team plan. */
    people?: number;
    allowance: Allowance;
    /** Whether this person can buy a plan here, which changes who the meter's
     *  nudge is addressed to. */
    canBuy: boolean;
    /** Stopping the renewal, where this person may. */
    stop?: { run: (done: () => void) => void; pending: boolean; failed: boolean } | null;
}) {
    const [asking, setAsking] = useState(false);
    const plan = subscription?.plan ?? null;
    const isPaying = paying(subscription);
    const name = isPaying ? shortName(plan!) : plan?.name ?? "Free";
    const until = onDate(subscription?.current_period_end);
    const seats = subscription?.seat_count ?? 0;

    const meta: string[] = [];
    if (isPaying) {
        meta.push(formatPrice(monthlyCents(plan!, seats), plan!.currency) + " a month");
        if (isPerSeat(plan!)) meta.push(seats + (seats === 1 ? " seat" : " seats"));
        if (subscription?.cancel_at_period_end) meta.push(until ? "ends " + until : "will not renew");
        else if (until) meta.push("renews " + until);
    } else {
        meta.push("Nothing is charged");
        if (orgName && people) meta.push(people + (people === 1 ? " person" : " people") + " in " + orgName);
    }

    const stoppable = Boolean(stop) && isPaying && !subscription?.cancel_at_period_end;

    return (
        <>
            <section className="kbill-now">
                <div className="kbill-now__plan">
                    <span className="kbill-now__k">Plan</span>
                    <b className="kbill-now__name">{name}</b>
                    <span className="kbill-now__meta">{meta.join(" · ")}</span>
                    {stoppable && (
                        <button type="button" className="b b--s b--secondary b--red kbill-now__stop" onClick={() => setAsking(true)}>
                            Stop renewing
                        </button>
                    )}
                </div>
                <Meter allowance={allowance} canBuy={canBuy} team={Boolean(orgName)} />
            </section>

            {asking && stop && (
                <ConfirmDialog
                    title={"Stop renewing " + name + "?"}
                    line={(orgName ? "Everyone in " + orgName + " keeps working" : "Access runs")
                        + (until ? " until " + until + ", the end of the period already paid for." : " to the end of the period already paid for.")
                        + " A stopped renewal can’t be restarted from here."}
                    confirm="Stop renewing"
                    working="Stopping…"
                    cancel="Keep it"
                    danger
                    busy={stop.pending}
                    problem={stop.failed ? "That couldn’t be stopped. Nothing has changed." : null}
                    onConfirm={() => stop.run(() => setAsking(false))}
                    onClose={() => setAsking(false)}
                />
            )}
        </>
    );
}

function Meter({ allowance, canBuy, team }: { allowance: Allowance; canBuy: boolean; team: boolean }) {
    if (allowance.kind === "uncapped") return null;
    const window = allowance.window;
    const blocked = allowance.kind === "blocked";
    const hot = blocked || (allowance.kind === "within" && allowance.warn);
    const resets = resetsIn(window.reset_at);
    const nudge = !hot ? ""
        : canBuy ? (team ? "A plan raises it for everyone here." : "A plan raises it.")
        : "An owner or editor can raise it with a plan.";
    const note = [resets && resets.charAt(0).toUpperCase() + resets.slice(1) + ".", nudge].filter(Boolean).join(" ");
    return (
        <div className={"kbill-meter" + (hot ? " kbill-meter--hot" : "")} role={hot ? "status" : undefined}>
            <div className="kbill-meter__top">
                <span>{blocked ? "Usage limit reached" : window.label}</span>
                <span className="kbill-fig">{formatPercent(window.used_percent, window.allowed)}</span>
            </div>
            {/* Capped at full: past the limit the number keeps climbing, and
                a bar that kept pace would leave its box. */}
            <span className="kmeter" aria-hidden="true">
                <i style={{ width: Math.min(100, Math.max(0, window.used_percent)) + "%" }} />
            </span>
            {note && <span className="kbill-meter__note">{note}</span>}
        </div>
    );
}
