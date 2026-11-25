"use client";

import { useEffect, useRef, useState } from "react";
import { Modal } from "@/shell/modal";
import { Bars } from "@/outcomes/parts";
import { CheckIcon, ExternalIcon } from "@/ui/icons";
import { captureEvent } from "@/site/analytics/client";
import {
    formatPrice, isPerSeat, isUpgrade, monthlyCents, onDate, shortName, usageBase, usageTimes, workspaceOf,
    runsDocker,
    type ChangeAcknowledged, type Plan, type StartedCheckout, type Subscription,
} from "./plan";

/** Saying yes to a plan, paying for it, and seeing it land.
 *
 *  One dialog from the card's button to the plan in force, because the person
 *  who pressed it is looking here the whole time. It says what they will pay
 *  before anything is charged — people × price, and when — then waits for the
 *  provider where they can see it waiting, and ends on the plan they bought.
 *
 *  A checkout is started the moment the dialog opens, not when its button is
 *  pressed. The provider's URL only exists after a request, and a tab opened
 *  after an await is a tab the browser blocks; started early, "Continue to
 *  payment" is a real link by the time anyone reaches for it, and one press
 *  opens the payment page. Starting one writes nothing: a checkout leaves no
 *  subscription until the payment lands, so closing the dialog without
 *  paying changes nothing.
 *
 *  A move between paid plans has no checkout. The card on file settles the
 *  difference, prorated, so the yes is the button itself, and the wait is
 *  for the provider to say the charge went through.
 */
type Phase = "confirm" | "paying" | "asked" | "landed";

export function PlanDialog({
    plan,
    move,
    current,
    plans,
    people,
    orgName,
    start,
    change,
    onWaiting,
    onClose,
}: {
    plan: Plan;
    move: "checkout" | "change";
    current: Subscription | null | undefined;
    /** The self-serve plans on offer, which usage is measured across. */
    plans: Plan[];
    /** Who a per-seat plan is charged for: everyone in the organization. */
    people: number;
    /** The organization's name on a team plan; absent for your own. */
    orgName?: string;
    start: (planId: string) => Promise<StartedCheckout>;
    change: (planId: string) => Promise<ChangeAcknowledged>;
    onWaiting: (waiting: boolean) => void;
    onClose: () => void;
}) {
    const [phase, setPhase] = useState<Phase>("confirm");
    const [checkout, setCheckout] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [problem, setProblem] = useState<string | null>(null);
    const begun = useRef(false);

    const name = shortName(plan);
    const was = current?.plan ?? null;
    const up = isUpgrade(was, plan);
    const team = isPerSeat(plan);
    const total = monthlyCents(plan, people);
    const money = (cents: number) => formatPrice(cents, plan.currency);
    const who = orgName ?? null;
    const landed = current?.plan_id === plan.id;

    async function begin() {
        setBusy(true);
        setProblem(null);
        try {
            const started = await start(plan.id);
            /* No URL is a button that would look live and do nothing. A free
               plan legitimately returns none, but nothing free is offered. */
            if (started.checkout_url) setCheckout(started.checkout_url);
            else setProblem("The payment page couldn’t be opened. Nothing has been charged.");
        } catch (failure) {
            setProblem(failure instanceof Error && failure.message
                ? failure.message
                : "The payment page couldn’t be opened. Nothing has been charged.");
        } finally {
            setBusy(false);
        }
    }

    /* Once per dialog. React runs effects twice in development, and two
       checkouts for one yes is one too many even when neither is paid. */
    useEffect(() => {
        if (move !== "checkout" || begun.current) return;
        begun.current = true;
        void begin();
    }, [move]);

    /* The payment landed: the subscription read is what notices, because
       nothing on this side happens when the provider's webhook arrives. */
    useEffect(() => {
        if ((phase === "paying" || phase === "asked") && landed) {
            setPhase("landed");
            onWaiting(false);
        }
    }, [phase, landed, onWaiting]);

    function close() {
        if (busy) return;
        if (phase === "paying" || phase === "asked") {
            captureEvent("billing.checkout_abandoned", { plan: plan.id });
            onWaiting(false);
        }
        onClose();
    }

    async function ask() {
        setBusy(true);
        setProblem(null);
        try {
            await change(plan.id);
            setPhase("asked");
            onWaiting(true);
        } catch (failure) {
            setProblem(failure instanceof Error && failure.message
                ? failure.message
                : "The plan couldn’t be changed. Nothing has been charged.");
        } finally {
            setBusy(false);
        }
    }

    if (phase === "landed") {
        return (
            <Modal
                title={(who ? who + " is on " : "You’re on ") + name}
                narrow
                onClose={onClose}
                footer={<button type="button" className="b b--l b--primary" onClick={onClose}>Done</button>}
            >
                <div className="kbill-landed">
                    <Bars size={56} />
                    <p>
                        {up
                            ? (who ? "Everyone’s usage limit went up just now." : "Your usage limit went up just now.")
                            : "The new limits apply from now."}
                        {current?.current_period_end && <> Next payment {money(total)} on {onDate(current.current_period_end)}.</>}
                    </p>
                </div>
            </Modal>
        );
    }

    if (phase === "paying" || phase === "asked") {
        const paying = phase === "paying";
        return (
            <Modal
                title={paying ? "Finish paying in the other tab" : "Moving to " + name}
                subtitle={paying
                    ? (who ?? "You") + " move" + (who ? "s" : "") + " to " + name + " the moment the payment lands. This page is watching for it."
                    : "The difference is being settled on the card you paid with. The plan moves once that goes through."}
                onClose={close}
                footer={paying ? <>
                    <button type="button" className="b b--l b--secondary" onClick={close}>Not now</button>
                    {checkout && (
                        <a className="b b--l b--secondary" href={checkout} target="_blank" rel="noreferrer">
                            Open the payment page again <ExternalIcon />
                        </a>
                    )}
                </> : <button type="button" className="b b--l b--secondary" onClick={close}>Close</button>}
            >
                <div className="kbill-wait" role="status">
                    <span className="kbill-ring" aria-hidden="true">
                        <svg viewBox="0 0 44 44"><circle cx="22" cy="22" r="19" /></svg>
                        <svg viewBox="0 0 44 44" className="kbill-ring__turn"><circle cx="22" cy="22" r="19" /></svg>
                    </span>
                    <span className="kbill-wait__t">
                        <b>Waiting for the payment</b>
                        <span>{money(total)} a month{team ? " for " + people + (people === 1 ? " person" : " people") : ""}</span>
                    </span>
                </div>
                {!paying && <p className="kbill-small">You can close this. The plan moves on its own.</p>}
            </Modal>
        );
    }

    /* What changes, from the plans' own limits, so it says only what is so. */
    const base = usageBase(plans);
    const times = usageTimes(plan, plans);
    const wasTimes = was ? usageTimes(was, plans) : null;
    const size = workspaceOf(plan);
    const docker = runsDocker(plan);
    const baseName = base ? shortName(base) : "";
    const gets: string[] = [];
    if (move === "change" && was) {
        if (times !== null && wasTimes !== null && times !== wasTimes) {
            gets.push("Usage " + (up ? "goes up" : "drops") + " from " + wasTimes + "× to " + times + "× " + baseName + "’s" + (team ? ", for each person" : ""));
        }
        if (size) gets.push("Workspaces " + (up ? "grow" : "shrink") + " to " + size.cores + " cores and " + size.gb + " GB");
        const joined = people - (current?.seat_count ?? people);
        if (team && joined > 0) {
            gets.push((joined === 1 ? "The person" : "The " + joined + " people") + " who joined after the last payment " + (joined === 1 ? "is" : "are") + " on the bill from now");
        }
    } else {
        if (times !== null && base && base.id !== plan.id) {
            gets.push(times + "× the usage of " + baseName + (team ? ", for each person" : ""));
        }
        if (size) gets.push("A " + size.cores + "-core, " + size.gb + " GB workspace" + (team ? " for each person" : "") + (docker ? ", with Docker" : ""));
        if (plan.features?.remove_lemma_branding === true) gets.push("Your branding instead of Lemma’s");
    }

    const verb = (up ? "Upgrade" : "Move") + " to " + name;
    const renews = onDate(current?.current_period_end);

    return (
        <Modal
            title={move === "checkout" && who ? "Upgrade " + who + " to " + name : verb}
            subtitle={who ? "Everyone in " + who + " moves together." : undefined}
            onClose={close}
            footer={<>
                <button type="button" className="b b--l b--secondary" onClick={close} disabled={busy && move === "change"}>
                    {move === "change" && was ? "Keep " + shortName(was) : "Not now"}
                </button>
                {move === "change" ? (
                    <button type="button" className="b b--l b--primary" disabled={busy} onClick={() => void ask()}>
                        {busy ? "Asking the card…" : verb}
                    </button>
                ) : checkout ? (
                    <a
                        className="b b--l b--primary"
                        href={checkout}
                        target="_blank"
                        rel="noreferrer"
                        onClick={() => {
                            captureEvent("billing.checkout_opened", { plan: plan.id });
                            setPhase("paying");
                            onWaiting(true);
                        }}
                    >
                        Continue to payment <ExternalIcon />
                    </a>
                ) : problem ? (
                    <button type="button" className="b b--l b--primary" onClick={() => void begin()}>Try again</button>
                ) : (
                    <button type="button" className="b b--l b--primary" disabled>Getting the payment page…</button>
                )}
            </>}
        >
            <div className="kbill-dlg">
                <div className="kbill-bill">
                    {move === "change" && was && (
                        <div className="kbill-bill__was">
                            <span>Now: {shortName(was)}{team ? ", " + current!.seat_count + (current!.seat_count === 1 ? " seat" : " seats") : ""}</span>
                            <span className="kbill-fig">{money(monthlyCents(was, current!.seat_count))} a month</span>
                        </div>
                    )}
                    <div className="kbill-bill__new">
                        <span>
                            {name}
                            {team && <>, {people} {people === 1 ? "person" : "people"} × <span className="kbill-fig">{money(plan.price_cents)}</span></>}
                        </span>
                        <span className="kbill-fig">{money(total)} a month</span>
                    </div>
                    {move === "checkout" && (
                        <div className="kbill-bill__due">
                            <span>Due today</span>
                            <span className="kbill-fig">{money(total)}</span>
                        </div>
                    )}
                </div>

                {gets.length > 0 && (
                    <ul className={"kbill-gets" + (up ? "" : " kbill-gets--down")}>
                        {gets.map((line) => <li key={line}><CheckIcon size={14} />{line}</li>)}
                    </ul>
                )}

                <p className="kbill-small">
                    {move === "checkout"
                        ? "Then " + money(total) + " every month. Stop renewing any time; access runs to the end of the month you paid for. You pay on our payment provider’s page, where any tax is added, and come back here."
                        : "The difference is settled today on the card you paid with, prorated for the days left. The plan moves once that payment goes through." + (renews ? " From " + renews + " you pay " + money(total) + " a month." : "")}
                </p>

                {problem && <p className="kd-problem" role="alert">{problem}</p>}
            </div>
        </Modal>
    );
}
