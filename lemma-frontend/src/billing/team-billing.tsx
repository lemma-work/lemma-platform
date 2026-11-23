"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshIcon, ExternalIcon } from "@/ui/icons";
import { lemma } from "@/session/client";
import { isForbidden } from "@/session/auth-state";
import { itemsOf } from "@/search/sources";
import { canManage, type Member } from "@/org/membership";
import { live, useMyLimits } from "@/usage/queries";
import { allowanceOf } from "@/usage/allowance";
import { BillingNow } from "./billing-now";
import { PlanCards } from "./plan-cards";
import { formatPrice, isTrouble, onDate, paying, standing } from "./plan";
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
 *  A member still sees the plans, with what each would cost everyone here. One
 *  you cannot buy yourself is still one you can ask an owner for, and a blank
 *  panel answers "what would it take to lift this limit?" with nothing.
 *
 *  Drawn as what the organization is on (`BillingNow`), the plans as cards to
 *  buy from (`PlanCards`), then the invoices as rows.
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

export function TeamBillingSection({ orgId, orgName }: { orgId: string; orgName?: string }) {
    const [waiting, setWaiting] = useState(false);

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
    /* Whether this person may buy is only known once both reads answer;
       until then nothing is offered, rather than a button the server may
       refuse. */
    const roleKnown = members.isSuccess && me.isSuccess;
    /* Who can buy, counted only from a roster read to its end: the first
       page of a larger organization would undercount them. */
    const wholeRoster = members.isSuccess && !(members.data as { next_page_token?: string | null }).next_page_token;
    const buyers = wholeRoster ? people.filter((member) => canManage(member.role)).length : 0;

    const plan = useOrgPlan(orgId, waiting);
    const plans = usePlans();
    const seats = useSeats(orgId);
    const limits = useMyLimits(orgId);
    const invoices = useInvoices(orgId, mayBuy);
    const start = useStartTeam(orgId);
    const change = useChangeTeam(orgId);
    const cancel = useCancelTeam(orgId);

    /* The sample workspace has no organization to bill, and the plans are
       still worth seeing: what there is, and what each would give. */
    if (!live()) {
        return (
            <div className="kset-stack kbill">
                <PlanCards
                    audience="TEAM"
                    plans={plans.data}
                    pending={plans.isPending}
                    current={null}
                    people={null}
                    crowded={false}
                    blocked={{
                        short: "Not in the sample workspace",
                        long: "The sample workspace has no organization to bill, so nothing here can be bought.",
                    }}
                    start={(planId) => start.mutateAsync(planId)}
                    change={(planId) => change.mutateAsync(planId)}
                    onWaiting={setWaiting}
                />
            </div>
        );
    }

    if (isForbidden(plan.error)) {
        return <p className="kset-note">Only members of this organization can see what it pays for.</p>;
    }

    const subscription = plan.data ?? null;
    const note = standing(subscription);
    const bought = seats.data?.current_seats ?? 0;
    /* Everyone in the organization, which is what a checkout charges for.
       Only the seat read counts all of them, so until it answers the head
       count is unknown, and nothing that depends on it is shown or sold. */
    const present = seats.data?.member_count ?? null;
    const where = orgName ?? "this organization";
    const blocked = !roleKnown
        ? (members.isError || me.isError
            ? {
                short: "Couldn’t read who can buy",
                long: <>Couldn’t read who can choose the plan for {where}.
                    <button type="button" className="b b--s b--secondary" onClick={() => { void members.refetch(); void me.refetch(); }}><RefreshIcon /> Try again</button></>,
            }
            : { short: "Checking who can buy…" })
        : !mayBuy
            ? {
                short: "An owner or editor can choose it",
                long: "Owners and editors choose the plan for " + where
                    + (buyers > 0 ? ". " + (buyers === 1 ? "One person" : buyers + " people") + " here can." : "."),
            }
            : present === null
                ? (seats.isError
                    ? {
                        short: "Couldn’t read how many people are here",
                        long: <>Couldn’t read how many people are in {where}, so the plans can’t be priced for it.
                            <button type="button" className="b b--s b--secondary" onClick={() => void seats.refetch()}><RefreshIcon /> Try again</button></>,
                    }
                    : { short: "Counting the people here…" })
                : undefined;
    const allowance = allowanceOf(limits.data);
    const crowded = allowance.kind === "blocked" || (allowance.kind === "within" && allowance.warn);
    const unpaid = present !== null && paying(subscription) && present > bought ? present - bought : 0;

    return (
        <div className="kset-stack kbill">
            {plan.isPending && <span className="ksk kbill-now--sk" aria-label="Reading the plan" />}
            {plan.isError && !isForbidden(plan.error) && (
                <p className="kset-said kset-said--bad" role="alert">
                    Couldn’t read the plan.
                    <button type="button" className="b b--s b--secondary" onClick={() => void plan.refetch()}><RefreshIcon /> Try again</button>
                </p>
            )}

            {!plan.isPending && !plan.isError && (
                <BillingNow
                    subscription={subscription}
                    orgName={orgName ?? "this organization"}
                    people={present ?? undefined}
                    allowance={allowance}
                    canBuy={mayBuy}
                    stop={mayStop ? {
                        run: (done) => cancel.mutate(undefined, { onSuccess: done }),
                        pending: cancel.isPending,
                        failed: cancel.isError,
                    } : null}
                />
            )}

            {note && <p className={"kset-said" + (isTrouble(subscription) ? " kset-said--bad" : "")}>{note}</p>}

            {/* Seats follow the member count only when the plan changes, so
                somebody who joined after the last payment is not on the bill
                yet. Said with what will put them on it. */}
            {unpaid > 0 && (
                <p className="kstatus kstatus--warn kset-state">
                    {unpaid === 1 ? "1 person" : unpaid + " people"} joined after the last payment, so {orgName ?? "this organization"} has {present} people
                    and pays for {bought}. They’re added to the bill when the plan next changes.
                </p>
            )}

            <PlanCards
                audience="TEAM"
                plans={plans.data}
                pending={plans.isPending}
                current={subscription}
                people={present}
                orgName={orgName}
                crowded={crowded}
                blocked={blocked}
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

            {mayBuy && (invoices.isPending || invoices.isError || (invoices.data && invoices.data.length > 0)) && <>
                <p className="ksub">Invoices</p>
                {invoices.isPending && <span className="ksk kbill-rows--sk" aria-label="Reading the invoices" />}
                {invoices.isError && (
                    <p className="kset-said kset-said--bad" role="alert">
                        Couldn’t read the invoices.
                        <button type="button" className="b b--s b--secondary" onClick={() => void invoices.refetch()}><RefreshIcon /> Try again</button>
                    </p>
                )}
                {(invoices.data ?? []).length > 0 && (
                    <div className="klist">
                        {(invoices.data ?? []).map((invoice) => (
                            <div className="krow" key={invoice.id}>
                                <span className="krow__v">
                                    <b>{onDate(invoice.period_start)} – {onDate(invoice.period_end)}</b>
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
