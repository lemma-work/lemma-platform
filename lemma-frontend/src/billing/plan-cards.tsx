"use client";

import { useState, type CSSProperties } from "react";
import { CheckIcon, ExternalIcon } from "@/ui/icons";
import { captureEvent } from "@/site/analytics/client";
import { PlanDialog } from "./plan-dialog";
import {
    compare, formatPrice, highlights, isContactSales, isPerSeat, isUpgrade, monthlyCents, moveTo,
    offered as offeredPlans, onDate, paying, priceUnit, recommended, shortName, tagline, usageBase,
    type ChangeAcknowledged, type Plan, type PlanAudience, type StartedCheckout, type Subscription,
} from "./plan";

/** The plans, as things you can buy.
 *
 *  Three cards side by side, each with its own button saying what it will do
 *  ("Upgrade to Plus"), its price on one line and, for a per-seat plan, what
 *  that comes to for everyone here — the figure a checkout actually charges.
 *  Under the price, only the rows where the plans differ, lined up across the
 *  cards so they can be read across; what every plan has is said once below.
 *  A plan sold by talking to us is a line under the cards, with a link to
 *  get in touch.
 */
export function PlanCards({
    audience,
    plans,
    pending,
    current,
    people,
    orgName,
    crowded,
    blocked,
    start,
    change,
    onWaiting,
}: {
    audience: PlanAudience;
    plans: Plan[] | undefined;
    /** The plan list is still being read. */
    pending: boolean;
    current: Subscription | null | undefined;
    /** Who a per-seat plan is charged for: everyone in the organization.
     *  Null where that is not known (the sample workspace), which leaves the
     *  total off rather than quoting it for nobody. */
    people: number | null;
    orgName?: string;
    /** Usage is near or at its limit, which earns the next plan up a word. */
    crowded: boolean;
    /** Why this person may look but not buy, short (in a card) and long
     *  (once, above them). Absent for somebody who can buy. */
    blocked?: { short: string; long: string };
    start: (planId: string) => Promise<StartedCheckout>;
    change: (planId: string) => Promise<ChangeAcknowledged>;
    onWaiting: (waiting: boolean) => void;
}) {
    const [target, setTarget] = useState<Plan | null>(null);

    const offered = offeredPlans(plans, audience);
    const selfServe = offered.filter((plan) => !isContactSales(plan));
    const contact = offered.find(isContactSales) ?? null;
    const { rows, shared } = compare(selfServe, audience);
    const pick = recommended(selfServe, current, crowded);
    const base = usageBase(selfServe);
    const onPlan = current?.plan ?? null;
    const isPaying = paying(current);
    /* The one solid button: the pick, or for somebody paying the next step up.
       Every other card's button is outlined, so the page has one way forward
       that stands out and never three. */
    const primaryId = pick?.plan.id
        ?? (isPaying ? selfServe.find((plan) => isUpgrade(onPlan, plan) && plan.id !== current?.plan_id)?.id : undefined);

    if (pending && offered.length === 0) {
        return (
            <div className="kbill-plans" style={{ "--n": 3 } as CSSProperties} aria-hidden="true">
                {[0, 1, 2].map((key) => <div key={key} className="kbill-plan kbill-plan--sk"><span className="ksk" /></div>)}
            </div>
        );
    }
    if (offered.length === 0) return null;

    const span = 5 + rows.length;
    const renews = onDate(current?.current_period_end);

    return (
        <>
            <p className="ksub">
                Plans
                {base && rows.some((row) => row.label === "Usage") && <span>Usage is measured against {shortName(base)}</span>}
            </p>

            {blocked && <p className="kbill-who"><span className="kstatus kstatus--off" aria-hidden="true" />{blocked.long}</p>}

            {selfServe.length > 0 && (
                <div className="kbill-plans" style={{ "--n": selfServe.length } as CSSProperties}>
                    {selfServe.map((plan, index) => {
                        const here = moveTo(current, plan) === "current";
                        const picked = !here && pick?.plan.id === plan.id;
                        const name = shortName(plan);
                        const verb = (isPaying && !isUpgrade(onPlan, plan) ? "Move to " : "Upgrade to ") + name;
                        return (
                            <article
                                key={plan.id}
                                className={"kbill-plan" + (here ? " kbill-plan--here" : "") + (picked ? " kbill-plan--pick" : "")}
                                style={{ "--rows": span } as CSSProperties}
                                aria-label={name}
                            >
                                <div className="kbill-plan__head">
                                    <span className="kbill-plan__name">{name}</span>
                                    {here && <span className="ktag">Your plan</span>}
                                    {picked && <span className="ktag kbill-tag--pick">{pick?.why === "more" ? "More usage" : "Recommended"}</span>}
                                </div>
                                <p className="kbill-plan__for">{tagline(plan)}</p>
                                <div className="kbill-plan__price">
                                    <b className="kbill-fig">{formatPrice(plan.price_cents, plan.currency)}</b>
                                    <span>{priceUnit(plan)}</span>
                                </div>
                                <p className="kbill-plan__total">
                                    {isPerSeat(plan) && people !== null && <>
                                        <b className="kbill-fig">{formatPrice(monthlyCents(plan, people), plan.currency)}</b> a month for
                                        {people === 1 ? " your 1 person" : " your " + people + " people"}
                                    </>}
                                </p>
                                <div className="kbill-plan__act">
                                    {here ? (
                                        <span className="kbill-plan__said">
                                            <CheckIcon size={14} />
                                            {current?.cancel_at_period_end
                                                ? (renews ? "Ends " + renews : "Will not renew")
                                                : renews ? "Renews " + renews : "Your plan"}
                                        </span>
                                    ) : blocked ? (
                                        <span className="kbill-plan__said">{blocked.short}</span>
                                    ) : (
                                        <button
                                            type="button"
                                            className={"b b--m " + (plan.id === primaryId ? "b--primary" : "b--secondary")}
                                            onClick={() => {
                                                captureEvent("billing.plan_chosen", { plan: plan.id });
                                                setTarget(plan);
                                            }}
                                        >{verb}</button>
                                    )}
                                </div>
                                {rows.map((row) => (
                                    <div className="kbill-plan__row" key={row.label}>
                                        <span>{row.label}</span>
                                        <span className={row.label === "Usage" ? "kbill-fig kbill-plan__times" : undefined}>{row.cells[index]}</span>
                                    </div>
                                ))}
                            </article>
                        );
                    })}
                </div>
            )}

            {shared.length > 0 && (
                <p className="kbill-every">
                    <span>On every plan</span>
                    {shared.map((line) => <span key={line}><CheckIcon size={13} />{line}</span>)}
                </p>
            )}

            {contact && (
                <div className="kbill-ent">
                    <div>
                        <b>{shortName(contact)}</b>
                        <p>{highlights(contact).join(" · ") || contact.description}</p>
                    </div>
                    <a className="b b--m b--secondary" href="/contact" target="_blank" rel="noreferrer">
                        Talk to us <ExternalIcon />
                    </a>
                </div>
            )}

            {target && (
                <PlanDialog
                    plan={target}
                    move={moveTo(current, target) === "change" ? "change" : "checkout"}
                    current={current}
                    plans={selfServe}
                    people={people ?? 1}
                    orgName={orgName}
                    start={start}
                    change={change}
                    onWaiting={onWaiting}
                    onClose={() => setTarget(null)}
                />
            )}
        </>
    );
}
