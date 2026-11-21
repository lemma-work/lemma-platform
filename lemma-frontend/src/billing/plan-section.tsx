"use client";

import { useState } from "react";
import { RefreshIcon } from "@/ui/icons";
import { live } from "@/usage/queries";
import { PlanPicker } from "./plan-picker";
import { isTrouble, onDate, paying, priceLine, standing } from "./plan";
import { useCancelPersonal, useChangePersonal, useMyPlan, usePlans, useStartPersonal } from "./queries";

/** What you pay, and what else is on offer.
 *
 *  Sits beside Usage rather than inside it, because the two answer different
 *  questions about the same money: that one is what you have spent, this one is
 *  what you may spend and what it costs to be allowed to spend more.
 *
 *  Personal only. An organization's plan is bought by the organization and
 *  lives under its own heading — the platform authorizes the two completely
 *  differently, and a screen that mixed them would have to explain why the
 *  buttons on half of it are missing.
 *
 *  Rows first — the plan, when it renews — then the plans as cards to pick
 *  from. Stopping a renewal is red ink until it is asked twice.
 */
export function PlanSection() {
    const [waiting, setWaiting] = useState(false);
    const [stopping, setStopping] = useState(false);
    const mine = useMyPlan(waiting);
    const plans = usePlans();
    const start = useStartPersonal();
    const change = useChangePersonal();
    const cancel = useCancelPersonal();

    /* The sample workspace reads the catalogue and sits on Free, so this whole
       screen can be looked at without a backend — which is where it gets looked
       at. What it cannot do is buy anything, and the picker is told why rather
       than left holding buttons that would fail. */
    const sample = !live();

    const subscription = mine.data ?? null;
    const plan = subscription?.plan ?? null;
    const note = standing(subscription);

    const stoppable = !sample && paying(subscription) && !subscription?.cancel_at_period_end;

    return (
        <div className="kset-stack">
            {mine.isPending && <p className="kset-note" role="status">Reading your plan…</p>}
            {mine.isError && (
                <p className="kset-said kset-said--bad" role="alert">
                    Your plan couldn’t be read.
                    <button type="button" className="b b--s b--secondary" onClick={() => void mine.refetch()}><RefreshIcon /> Try again</button>
                </p>
            )}

            {!mine.isPending && !mine.isError && (
                <div className="klist">
                    <div className="krow krow--kv">
                        <span className="krow__k">Plan</span>
                        <span className="krow__v kset-wrap">
                            <b>{plan?.name ?? "No plan yet"}</b>
                            <span>
                                {/* Free and no subscription at all are one
                                    sentence, because they are one situation
                                    to the person reading it: nothing is being
                                    charged. A 404 here is ordinary rather than
                                    a fault — the free subscription is written
                                    the first time an agent runs, so somebody
                                    who has not used one has no row. And the
                                    price for a free plan would read "Free"
                                    directly under the word Free. */}
                                {paying(subscription)
                                    ? priceLine(plan!)
                                    : "Nothing is charged on this account. A plan raises the limits you work under."}
                            </span>
                        </span>
                        <span className="krow__end">
                            {stoppable && !stopping && (
                                <button type="button" className="b b--s b--secondary b--red" onClick={() => setStopping(true)}>Stop renewing</button>
                            )}
                        </span>
                    </div>
                    {subscription?.current_period_end && !subscription.cancel_at_period_end && paying(subscription) && (
                        <div className="krow krow--kv">
                            <span className="krow__k">Renews</span>
                            <span className="krow__v"><b>{onDate(subscription.current_period_end)}</b></span>
                            <span className="krow__end" />
                        </div>
                    )}
                </div>
            )}

            {note && <p className={"kset-said" + (isTrouble(subscription) ? " kset-said--bad" : "")}>{note}</p>}

            {stoppable && stopping && (
                <div className="kset-stack" role="group" aria-label="Stop renewing">
                    <p className="kset-said">Stop renewing {plan?.name}? Access runs to the end of the period you have paid for.</p>
                    <div className="kset-acts">
                        {/* Filled, in the colour of a consequence: there is no
                            endpoint that resumes a subscription, so this is
                            not a click anybody can take back from here. */}
                        <button
                            type="button"
                            className="b b--s b--danger"
                            disabled={cancel.isPending}
                            onClick={() => { cancel.mutate(undefined, { onSuccess: () => setStopping(false) }); }}
                        >{cancel.isPending ? "Stopping…" : "Stop renewing"}</button>
                        <button type="button" className="b b--s b--secondary" onClick={() => setStopping(false)}>Keep it</button>
                    </div>
                    {cancel.isError && <p className="kset-said kset-said--bad" role="alert">That could not be cancelled. Nothing has changed.</p>}
                </div>
            )}

            <p className="ksub">Plans</p>
            <PlanPicker
                audience="PERSONAL"
                plans={plans.data}
                current={subscription}
                pending={plans.isPending}
                start={(planId) => start.mutateAsync(planId)}
                change={(planId) => change.mutateAsync(planId)}
                onWaiting={setWaiting}
                disabled={sample ? "The sample workspace has no account to bill, so nothing here can be bought." : undefined}
            />
            {plans.isError && (
                <p className="kset-said kset-said--bad" role="alert">
                    The plans couldn’t be read.
                    <button type="button" className="b b--s b--secondary" onClick={() => void plans.refetch()}><RefreshIcon /> Try again</button>
                </p>
            )}
        </div>
    );
}
