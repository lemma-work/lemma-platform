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
 *
 *  Drawn as rows (the plan, when it renews, the seats), the plans as cards,
 *  then the invoices as rows with their state as a dot and a word.
 */
function statusWord(status: string): string {
    if (status === "paid") return "Paid";
    if (status === "unpaid") return "Due";
    if (status === "failed") return "Payment failed";
    if (status === "void") return "Void";
    return "Draft";
}

/** The dot beside that word: paid is green, due is warm, failed is red, the
 *  rest grey. */
function statusTone(status: string): string {
    if (status === "paid") return "kstatus";
    if (status === "unpaid") return "kstatus kstatus--warn";
    if (status === "failed") return "kstatus kstatus--bad";
    return "kstatus kstatus--off";
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
        return <p className="kset-note">The sample workspace has no real organization, so there is nothing to bill.</p>;
    }

    if (isForbidden(plan.error)) {
        return <p className="kset-note">Only members of this organization can see what it pays for.</p>;
    }

    const subscription = plan.data ?? null;
    const onPlan = subscription?.plan ?? null;
    const note = standing(subscription);
    const bought = seats.data?.current_seats ?? 0;
    const present = seats.data?.member_count ?? 0;

    const stoppable = mayStop && paying(subscription) && !subscription?.cancel_at_period_end;

    return (
        <div className="kset-stack">
            {plan.isPending && <p className="kset-note" role="status">Reading the plan…</p>}
            {plan.isError && !isForbidden(plan.error) && (
                <p className="kset-said kset-said--bad" role="alert">
                    The plan couldn’t be read.
                    <button type="button" className="b b--s b--secondary" onClick={() => void plan.refetch()}><RefreshIcon /> Try again</button>
                </p>
            )}

            {(!plan.isPending && !plan.isError) || seats.data ? (
                <div className="klist">
                    {!plan.isPending && !plan.isError && <>
                        <div className="krow krow--kv">
                            <span className="krow__k">Plan</span>
                            <span className="krow__v kset-wrap">
                                <b>{onPlan?.name ?? "No plan yet"}</b>
                                {/* As on the personal screen: free and unbought
                                    are the same sentence, and a price reading
                                    "Free" under the word Free says nothing
                                    twice. */}
                                <span>{paying(subscription) ? priceLine(onPlan!) : "This organization has not bought a plan."}</span>
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
                    </>}
                    {seats.data && (
                        /* Two numbers, because they answer different questions
                           and only their difference is worth acting on: a team
                           that grew mid-period has more people than it has
                           paid for, and nothing charges for the difference
                           until the plan is next changed. */
                        <div className="krow krow--kv">
                            <span className="krow__k">Seats</span>
                            <span className="krow__v kset-wrap">
                                <b>Paid for {bought} {bought === 1 ? "seat" : "seats"}</b>
                                <span>{present} {present === 1 ? "person" : "people"} in the organization</span>
                            </span>
                            <span className="krow__end">
                                {present > bought && paying(subscription) && (
                                    <span className="kstatus kstatus--warn">{present - bought} not yet paid for</span>
                                )}
                            </span>
                        </div>
                    )}
                </div>
            ) : null}

            {note && <p className={"kset-said" + (isTrouble(subscription) ? " kset-said--bad" : "")}>{note}</p>}

            {stoppable && stopping && (
                <div className="kset-stack" role="group" aria-label="Stop renewing">
                    <p className="kset-said">Stop renewing {onPlan?.name}? Everyone here keeps working until the end of the period already paid for.</p>
                    <div className="kset-acts">
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

            <p className="ksub">Plans <span>A month, for each person in the organization</span></p>
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
            {plans.isError && (
                <p className="kset-said kset-said--bad" role="alert">
                    The plans couldn’t be read.
                    <button type="button" className="b b--s b--secondary" onClick={() => void plans.refetch()}><RefreshIcon /> Try again</button>
                </p>
            )}

            {mayBuy && (invoices.isPending || invoices.isError || (invoices.data && invoices.data.length > 0)) && <>
                <p className="ksub">Invoices</p>
                {invoices.isPending && <p className="kset-note" role="status">Reading the history…</p>}
                {invoices.isError && (
                    <p className="kset-said kset-said--bad" role="alert">
                        The invoices couldn’t be read.
                        <button type="button" className="b b--s b--secondary" onClick={() => void invoices.refetch()}><RefreshIcon /> Try again</button>
                    </p>
                )}
                {(invoices.data ?? []).length > 0 && (
                    <div className="klist">
                        {(invoices.data ?? []).map((invoice) => (
                            <div className="krow" key={invoice.id}>
                                <span className="krow__v">
                                    <b>{onDate(invoice.period_start)} — {onDate(invoice.period_end)}</b>
                                    <span className={statusTone(invoice.status)}>{statusWord(invoice.status)}</span>
                                </span>
                                <span className="krow__end">
                                    <span className="kset-fig">{formatPrice(invoice.total_cents, invoice.currency)}</span>
                                    {invoice.status === "unpaid" && invoice.checkout_url && (
                                        <a className="b b--s b--secondary" href={invoice.checkout_url} target="_blank" rel="noreferrer">Pay <ExternalIcon /></a>
                                    )}
                                </span>
                            </div>
                        ))}
                    </div>
                )}
            </>}
        </div>
    );
}
