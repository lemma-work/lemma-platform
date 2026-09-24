"use client";

import { LoadingIndicator } from "@/ui/loading";

import { useEffect, useState } from "react";
import { CheckIcon, ExternalIcon, RefreshIcon } from "@/ui/icons";
import {
    highlights, isContactSales, moveTo, offered as offeredPlans, priceLine,
    type ChangeAcknowledged, type Plan, type PlanAudience, type StartedCheckout, type Subscription,
} from "./plan";

/** Choosing a plan, and the wait that follows.
 *
 *  One component for both audiences because the two differ only in which calls
 *  they make: a personal subscription and a team's are bought the same way,
 *  through a checkout the customer completes somewhere else.
 *
 *  The wait is the part worth getting right. Nothing about a plan changes until
 *  the provider's webhook arrives, so there is no moment on this side to react
 *  to — the payment finishes in another tab and this one finds out by asking.
 *  That is the same shape as authorising an account in `shell/reach.tsx`, and
 *  it says the same thing out loud: the work is over there, and this page
 *  notices when you are done.
 *
 *  The link is a link the person clicks, not a window this opens for them. A
 *  popup opened after an await is a popup the browser blocks, and the failure
 *  would land on the one control in the app whose job is to take money.
 */
export function PlanPicker({
    audience,
    plans,
    current,
    pending,
    start,
    change,
    onWaiting,
    disabled,
}: {
    audience: PlanAudience;
    plans: Plan[] | undefined;
    current: Subscription | null | undefined;
    /** Whether the plan list is still being read, so an empty grid reads as
     *  loading rather than as a catalogue with nothing in it. */
    pending: boolean;
    start: (planId: string) => Promise<StartedCheckout>;
    change: (planId: string) => Promise<ChangeAcknowledged>;
    /** Turns the parent's poll on. The subscription read is what learns the
     *  payment landed, and it only learns it if it keeps asking. */
    onWaiting: (waiting: boolean) => void;
    /** Set where the person may look but not buy — a member reading their
     *  organization's billing. The cards still render: a plan you cannot
     *  choose yourself is still one you can ask somebody for. */
    disabled?: string;
}) {
    const [chosen, setChosen] = useState<string | null>(null);
    const [checkout, setCheckout] = useState<string | null>(null);
    const [asked, setAsked] = useState(false);
    const [busy, setBusy] = useState<string | null>(null);
    const [problem, setProblem] = useState<string | null>(null);

    const offered = offeredPlans(plans, audience);
    const settled = chosen !== null && current?.plan_id === chosen;

    /* The payment landed. Everything about this component is about a decision
       that has now been made, so it goes back to being a list of plans. */
    useEffect(() => {
        if (!settled) return;
        setChosen(null);
        setCheckout(null);
        setAsked(false);
        onWaiting(false);
    }, [settled, onWaiting]);

    async function pick(plan: Plan) {
        const move = moveTo(current, plan);
        if (move === "current" || move === "contact") return;
        setBusy(plan.id);
        setProblem(null);
        try {
            if (move === "change") {
                await change(plan.id);
                setAsked(true);
            } else {
                const started = await start(plan.id);
                /* No URL and no subscription would be a button that looked
                   live and did nothing. A free plan legitimately returns no
                   checkout, but nothing free is offered here. */
                if (!started.checkout_url) {
                    setProblem("That plan could not be started. Nothing has been charged.");
                    setBusy(null);
                    return;
                }
                setCheckout(started.checkout_url);
            }
            setChosen(plan.id);
            onWaiting(true);
        } catch (failure) {
            setProblem(failure instanceof Error ? failure.message : "That could not be started.");
        } finally {
            setBusy(null);
        }
    }

    return (
        <div className="plans">
            {(checkout || asked) && (
                <div className="plans__waiting" role="status">
                    {checkout && (
                        <a className="btn btn--primary" href={checkout} target="_blank" rel="noreferrer">
                            Complete the payment <ExternalIcon size={14} />
                        </a>
                    )}
                    <p>
                        {checkout
                            ? "The payment happens at our provider. Nothing changes here until it goes through — this page notices when it does."
                            : "Asked for. The card on file settles the difference, and the plan moves once the payment does."}
                    </p>
                    <span className="plans__wait"><RefreshIcon size={13} /> Waiting for the payment…</span>
                    {/* A way out for somebody who changed their mind. Without
                        it, closing the payment tab leaves this panel waiting on
                        a payment that is never coming, with the plans behind it
                        disabled — and the only exit is to leave settings. It
                        stops the asking; it does not cancel anything, because
                        there is nothing here to cancel: an abandoned checkout
                        writes nothing at either end. */}
                    <button className="plans__give-up" onClick={() => {
                        setChosen(null);
                        setCheckout(null);
                        setAsked(false);
                        onWaiting(false);
                    }}>Not now</button>
                </div>
            )}

            {problem && <p className="plans__problem">{problem}</p>}

            {pending && offered.length === 0 && <p className="usage-quiet" role="status">Reading the plans…</p>}

            <div className="plans__grid">
                {offered.map((plan) => {
                    const move = moveTo(current, plan);
                    const here = move === "current";
                    return (
                        <article className="plan" key={plan.id} data-current={here}>
                            <h5>{plan.name}</h5>
                            <p className="plan__price">{priceLine(plan)}</p>
                            {plan.description && <p className="plan__blurb">{plan.description}</p>}
                            <ul className="plan__list">
                                {highlights(plan).map((line) => (
                                    <li key={line}><CheckIcon size={13} />{line}</li>
                                ))}
                            </ul>
                            {here ? (
                                <span className="plan__here">Current plan</span>
                            ) : isContactSales(plan) ? (
                                <span className="plan__here">Talk to us</span>
                            ) : (
                                <button
                                    className="btn"
                                    disabled={Boolean(disabled) || busy !== null || chosen !== null}
                                    title={disabled}
                                    onClick={() => void pick(plan)}
                                >
                                    {busy === plan.id ? <LoadingIndicator inline label="Loading checkout" /> : move === "change" ? "Switch to this" : "Choose"}
                                </button>
                            )}
                        </article>
                    );
                })}
            </div>

            {disabled && <p className="usage-quiet">{disabled}</p>}
        </div>
    );
}
