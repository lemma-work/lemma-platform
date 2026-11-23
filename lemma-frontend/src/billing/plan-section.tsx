"use client";

import { useState } from "react";
import { RefreshIcon } from "@/ui/icons";
import { live, useMyLimits } from "@/usage/queries";
import { allowanceOf } from "@/usage/allowance";
import { BillingNow } from "./billing-now";
import { PlanCards } from "./plan-cards";
import { isTrouble, standing } from "./plan";
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
 *  The same shape as the organization's: what you are on (`BillingNow`), then
 *  the plans as cards to buy from (`PlanCards`).
 */
export function PlanSection() {
    const [waiting, setWaiting] = useState(false);
    const mine = useMyPlan(waiting);
    const plans = usePlans();
    const limits = useMyLimits();
    const start = useStartPersonal();
    const change = useChangePersonal();
    const cancel = useCancelPersonal();

    /* The sample workspace reads the catalogue and sits on Free, so this whole
       screen can be looked at without a backend — which is where it gets looked
       at. What it cannot do is buy anything, and the cards say why rather than
       holding buttons that would fail. */
    const sample = !live();

    const subscription = mine.data ?? null;
    const note = standing(subscription);
    const allowance = allowanceOf(limits.data);
    const crowded = allowance.kind === "blocked" || (allowance.kind === "within" && allowance.warn);

    return (
        <div className="kset-stack kbill">
            {mine.isPending && <span className="ksk kbill-now--sk" aria-label="Reading your plan" />}
            {mine.isError && (
                <p className="kset-said kset-said--bad" role="alert">
                    Couldn’t read your plan.
                    <button type="button" className="b b--s b--secondary" onClick={() => void mine.refetch()}><RefreshIcon /> Try again</button>
                </p>
            )}

            {!mine.isPending && !mine.isError && (
                <BillingNow
                    subscription={subscription}
                    allowance={allowance}
                    canBuy={!sample}
                    stop={sample ? null : {
                        run: (done) => cancel.mutate(undefined, { onSuccess: done }),
                        pending: cancel.isPending,
                        failed: cancel.isError,
                    }}
                />
            )}

            {note && <p className={"kset-said" + (isTrouble(subscription) ? " kset-said--bad" : "")}>{note}</p>}

            <PlanCards
                audience="PERSONAL"
                plans={plans.data}
                pending={plans.isPending}
                current={subscription}
                people={1}
                crowded={crowded}
                blocked={sample ? {
                    short: "Not in the sample workspace",
                    long: "The sample workspace has no account to bill, so nothing here can be bought.",
                } : undefined}
                start={(planId) => start.mutateAsync(planId)}
                change={(planId) => change.mutateAsync(planId)}
                onWaiting={setWaiting}
            />
            {plans.isError && (
                <p className="kset-said kset-said--bad" role="alert">
                    Couldn’t read the plans.
                    <button type="button" className="b b--s b--secondary" onClick={() => void plans.refetch()}><RefreshIcon /> Try again</button>
                </p>
            )}
        </div>
    );
}
