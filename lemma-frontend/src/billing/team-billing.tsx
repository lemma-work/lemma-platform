"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshIcon, ExternalIcon } from "@/ui/icons";
import { lemma } from "@/session/client";
import { isForbidden } from "@/session/auth-state";
import { itemsOf } from "@/search/sources";
import { canManage, type Member } from "@/org/membership";
import { live } from "@/usage/queries";
import { PlanPicker } from "./plan-picker";
import { formatPrice, isTrouble, onDate, paying, priceLine, standing } from "./plan";
import {
    useCancelTeam, useChangeTeam, useInvoices, useOrgPlan, usePlans, useSeats, useStartTeam,
} from "./queries";

/** What the organization pays for, and who may change it.
 *
 *  Three different permissions on one screen, because the platform holds three:
 *  any member may read the plan and the seats, an owner or editor may buy and
 *  switch, and only an owner may stop it. They are gated here so the screen
 *  does not offer an action the server has already decided to refuse — the rule
 *  `org/membership.ts` states for the roster, applied to money.
 *
 *  A member still sees the plans. One you cannot buy yourself is still one you
 *  can ask an owner for, and a blank panel answers "what would it take to lift
 *  this limit?" with nothing.
 */
function statusWord(status: string): string {
    if (status === "paid") return "Paid";
    if (status === "unpaid") return "Due";
    if (status === "failed") return "Payment failed";
    if (status === "void") return "Void";
    return "Draft";
}

export function TeamBillingSection({ orgId }: { orgId: string }) {
    const [waiting, setWaiting] = useState(false);
    const [stopping, setStopping] = useState(false);

    /* The same query keys the roster uses, so looking at billing costs no extra
       requests for anybody who has already opened People — and changing a role
       there is reflected here without either screen knowing about the other. */
    const me = useQuery({
        queryKey: ["current-user"],
        queryFn: () => lemma().users.current(),
        enabled: live(),
        staleTime: 5 * 60_000,
    });
    const members = useQuery({
        queryKey: ["org-members", orgId],
        queryFn: () => lemma().organizations.members.list(orgId, { limit: 100 }),
        enabled: live(),
    });

    const people = itemsOf(members.data) as unknown as Member[];
    const myRole = people.find((member) => member.user_id === me.data?.id)?.role ?? null;
    const mayBuy = canManage(myRole);
    const mayStop = myRole === "ORG_OWNER";

    const plan = useOrgPlan(orgId, waiting);
    const plans = usePlans();
    const seats = useSeats(orgId);
    const invoices = useInvoices(orgId, mayBuy);
    const start = useStartTeam(orgId);
    const change = useChangeTeam(orgId);
    const cancel = useCancelTeam(orgId);

    if (!live()) {
        return <p className="usage-quiet">The sample workspace has no real organization, so there is nothing to bill.</p>;
    }

    if (isForbidden(plan.error)) {
        return <p className="usage-quiet">Only members of this organization can see what it pays for.</p>;
    }

    const subscription = plan.data ?? null;
    const onPlan = subscription?.plan ?? null;
    const note = standing(subscription);
    const bought = seats.data?.current_seats ?? 0;
    const present = seats.data?.member_count ?? 0;

    return (
        <div className="usage-panel">
            <section className="usage-block">
                <h4>What this organization is on</h4>

                {plan.isPending && <p className="usage-quiet" role="status">Reading the plan…</p>}
                {plan.isError && !isForbidden(plan.error) && (
                    <p className="usage-quiet">
                        The plan could not be read.{" "}
                        <button className="btn" onClick={() => void plan.refetch()}><RefreshIcon size={14} /> Try again</button>
                    </p>
                )}

                {!plan.isPending && !plan.isError && (
                    <div className="plan-now">
                        <strong>{onPlan?.name ?? "No plan yet"}</strong>
                        <span className="plan-now__price">
                            {/* As on the personal screen: free and unbought are
                                the same sentence, and a price line reading
                                "Free" under the word Free says nothing twice. */}
                            {paying(subscription) ? priceLine(onPlan!) : "This organization has not bought a plan."}
                        </span>
                        {subscription?.current_period_end && !subscription.cancel_at_period_end && paying(subscription) && (
                            <small>Renews {onDate(subscription.current_period_end)}</small>
                        )}
                    </div>
                )}

                {note && <p className="plan-note" data-bad={isTrouble(subscription)}>{note}</p>}

                {seats.data && (
                    <p className="plan-seats">
                        {/* Two numbers, because they answer different questions
                            and only their difference is worth acting on: a team
                            that grew mid-period has more people than it has
                            paid for, and nothing charges for the difference
                            until the plan is next changed. */}
                        Paid for {bought} {bought === 1 ? "seat" : "seats"} · {present} {present === 1 ? "person" : "people"} in the organization
                        {present > bought && paying(subscription) && (
                            <em> — {present - bought} not yet paid for.</em>
                        )}
                    </p>
                )}

                {mayStop && paying(subscription) && !subscription?.cancel_at_period_end && (
                    stopping ? (
                        <div className="plan-stop">
                            <p>Stop renewing {onPlan?.name}? Everyone here keeps working until the end of the period already paid for.</p>
                            <div className="plan-stop__actions">
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
                    audience="TEAM"
                    plans={plans.data}
                    current={subscription}
                    pending={plans.isPending}
                    start={(planId) => start.mutateAsync(planId)}
                    change={(planId) => change.mutateAsync(planId)}
                    onWaiting={setWaiting}
                    disabled={members.isSuccess && !mayBuy
                        ? "An owner or an editor buys and changes the plan for this organization."
                        : undefined}
                />
            </section>

            {mayBuy && (invoices.isPending || (invoices.data && invoices.data.length > 0)) && (
                <section className="usage-block">
                    <h4>Invoices</h4>
                    {invoices.isPending && <p className="usage-quiet" role="status">Reading the history…</p>}
                    <ul className="usage-breakdown">
                        {(invoices.data ?? []).map((invoice) => (
                            <li key={invoice.id}>
                                <span>
                                    {onDate(invoice.period_start)} — {onDate(invoice.period_end)}
                                    {" · "}{statusWord(invoice.status)}
                                </span>
                                <span className="usage-breakdown__cost">
                                    {formatPrice(invoice.total_cents, invoice.currency)}
                                    {invoice.status === "unpaid" && invoice.checkout_url && (
                                        <> <a href={invoice.checkout_url} target="_blank" rel="noreferrer">Pay <ExternalIcon size={12} /></a></>
                                    )}
                                </span>
                            </li>
                        ))}
                    </ul>
                </section>
            )}
        </div>
    );
}
