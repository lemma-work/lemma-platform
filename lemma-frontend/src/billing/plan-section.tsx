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

    return (
        <div className="usage-panel">
            <section className="usage-block">
                <h4>What you are on</h4>

                {mine.isPending && <p className="usage-quiet" role="status">Reading your plan…</p>}
                {mine.isError && (
                    <p className="usage-quiet">
                        Your plan could not be read.{" "}
                        <button className="btn" onClick={() => void mine.refetch()}><RefreshIcon size={14} /> Try again</button>
                    </p>
                )}

                {!mine.isPending && !mine.isError && (
                    <div className="plan-now">
                        <strong>{plan?.name ?? "No plan yet"}</strong>
                        <span className="plan-now__price">
                            {/* Free and no subscription at all are one sentence,
                                because they are one situation to the person
                                reading it: nothing is being charged. A 404 here
                                is ordinary rather than a fault — the free
                                subscription is written the first time an agent
                                runs, so somebody who has not used one has no row.
                                And the price line for a free plan would read
                                "Free" directly under the word Free. */}
                            {paying(subscription)
                                ? priceLine(plan!)
                                : "Nothing is charged on this account. A plan raises the limits you work under."}
                        </span>
                        {subscription?.current_period_end && !subscription.cancel_at_period_end && paying(subscription) && (
                            <small>Renews {onDate(subscription.current_period_end)}</small>
                        )}
                    </div>
                )}

                {note && <p className="plan-note" data-bad={isTrouble(subscription)}>{note}</p>}

                {!sample && paying(subscription) && !subscription?.cancel_at_period_end && (
                    stopping ? (
                        <div className="plan-stop">
                            <p>Stop renewing {plan?.name}? Access runs to the end of the period you have paid for.</p>
                            <div className="plan-stop__actions">
                                {/* Filled, because it is the answering half of a
                                    pair and an outlined one beside "Keep it"
                                    leaves neither reading as the answer. In the
                                    colour of a consequence rather than a
                                    primary action: there is no endpoint that
                                    resumes a subscription, so this is not a
                                    click anybody can take back from here. */}
                                <button
                                    className="btn btn--danger"
                                    disabled={cancel.isPending}
                                    onClick={() => { cancel.mutate(undefined, { onSuccess: () => setStopping(false) }); }}
                                >{cancel.isPending ? "Stopping…" : "Stop renewing"}</button>
                                <button className="btn" onClick={() => setStopping(false)}>Keep it</button>
                            </div>
                            {cancel.isError && <p className="plans__problem">That could not be cancelled. Nothing has changed.</p>}
                        </div>
                    ) : (
                        <button className="plan-stop__ask" onClick={() => setStopping(true)}>Stop renewing</button>
                    )
                )}
            </section>

            <section className="usage-block">
                <h4>Plans</h4>
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
                {plans.isError && <p className="usage-quiet">The plans could not be read.</p>}
            </section>
        </div>
    );
}
