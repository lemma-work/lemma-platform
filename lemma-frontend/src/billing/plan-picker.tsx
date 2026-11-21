"use client";

import { LoadingIndicator } from "@/ui/loading";

import { useEffect, useId, useState } from "react";
import { CheckIcon, ExternalIcon } from "@/ui/icons";
import { captureEvent } from "@/site/analytics/client";
import {
    highlights, moveTo, offered as offeredPlans, priceLine,
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
 *
 *  The plans are cards (the kit's `.kopts`): the one you are on is picked to
 *  begin with, picking another tints it, and the one button under them says
 *  what it will do with that plan. A plan sold by talking to us says so
 *  instead of offering a button that cannot sell it.
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
    const [picked, setPicked] = useState<string | null>(null);
    const group = useId();

    const offered = offeredPlans(plans, audience);
    const selected = offered.find((plan) => plan.id === picked)
        ?? offered.find((plan) => moveTo(current, plan) === "current")
        ?? null;
    const selectedMove = selected ? moveTo(current, selected) : null;
    const waiting = Boolean(checkout || asked);
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
        <div className="kset-stack">
            {waiting && (
                <div className="kset-stack" role="status">
                    <p className="kstatus kstatus--warn">Waiting for the payment</p>
                    <p className="kset-said">
                        {checkout
                            ? "The payment happens at our provider. Nothing changes here until it goes through — this page notices when it does."
                            : "Asked for. The card on file settles the difference, and the plan moves once the payment does."}
                    </p>
                    <div className="kset-acts">
                        {checkout && (
                            <a className="b b--m b--primary" href={checkout} target="_blank" rel="noreferrer"
                                onClick={() => captureEvent("billing.checkout_opened", { plan: chosen ?? "" })}>
                                Complete the payment <ExternalIcon />
                            </a>
                        )}
                        {/* A way out for somebody who changed their mind.
                            Without it, closing the payment tab leaves this
                            waiting on a payment that is never coming, with the
                            plans behind it disabled — and the only exit is to
                            leave settings. It stops the asking; it does not
                            cancel anything, because there is nothing here to
                            cancel: an abandoned checkout writes nothing at
                            either end. */}
                        <button type="button" className="b b--m b--quiet" onClick={() => {
                            captureEvent("billing.checkout_abandoned", { plan: chosen ?? "" });
                            setChosen(null);
                            setCheckout(null);
                            setAsked(false);
                            onWaiting(false);
                        }}>Not now</button>
                    </div>
                </div>
            )}

            {pending && offered.length === 0 && <p className="kset-note" role="status">Reading the plans…</p>}

            {offered.length > 0 && (
                <div className="kopts kset-plans" role="radiogroup" aria-label="Plans">
                    {offered.map((plan) => {
                        const here = moveTo(current, plan) === "current";
                        return (
                            <label className="kopt kopt--compact" key={plan.id}>
                                <input
                                    type="radio"
                                    name={group}
                                    checked={selected?.id === plan.id}
                                    disabled={waiting || busy !== null}
                                    onChange={() => { setProblem(null); setPicked(plan.id); }}
                                />
                                <span className="kopt__t">{plan.name}{here && <span className="ktag">Current</span>}</span>
                                {plan.description && <span className="kopt__d">{plan.description}</span>}
                                <span className="kset-price">{priceLine(plan)}</span>
                                <span className="kset-plan-has">
                                    {highlights(plan).map((line) => (
                                        <span key={line}><CheckIcon size={13} />{line}</span>
                                    ))}
                                </span>
                                <span className="kopt__dot" aria-hidden="true" />
                            </label>
                        );
                    })}
                </div>
            )}

            {problem && <p className="kset-said kset-said--bad" role="alert">{problem}</p>}

            {selected && !waiting && selectedMove === "contact" && (
                <p className="kset-note">Talk to us to move to {selected.name}.</p>
            )}
            {selected && !waiting && (selectedMove === "change" || selectedMove === "checkout") && (
                <div className="kset-acts">
                    <button
                        type="button"
                        className="b b--m b--primary"
                        disabled={Boolean(disabled) || busy !== null || chosen !== null}
                        title={disabled}
                        onClick={() => void pick(selected)}
                    >
                        {busy === selected.id
                            ? <LoadingIndicator inline label="Loading checkout" />
                            : (selectedMove === "change" ? "Switch to " : "Choose ") + selected.name}
                    </button>
                </div>
            )}

            {disabled && <p className="kset-note">{disabled}</p>}
        </div>
    );
}
