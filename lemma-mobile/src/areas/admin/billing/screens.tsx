import { useMemo, useRef, useState } from "react";
import { Platform, View } from "react-native";
import { useRouter } from "expo-router";
import { useOrg } from "../../../data/hooks";
import { useFeature } from "../../../session/features";
import { useSession } from "../../../session/session";
import type { Org } from "../../../data/types";
import { Button } from "../../../ui/press";
import { Counted } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { useDeployment, useMyOrgRole } from "../access/hooks";
import { openOutside } from "../connect/round-trip";
import { adminRoutes } from "../routes";
import { Field, Group, ReadProblem, Reading, Row, Say, SettingsScreen, isForbidden, problemOf } from "../ui";
import {
    useCancel, useChange, useContactsCap, useDaily, useInvoices, useMyLimits, useOnReturn, usePlans, useSeats,
    useNoBilling, useSetContactsCap, useSettle, useSpend, useStart, useSubscription,
} from "./hooks";
import { Current, EndPress, Figure, Slices, SpendGroup, StopRenewing, WindowMeter } from "./parts";
import { PlanPicker } from "./picker";
import type { SpendScope } from "./source";
import { allowanceOf, planLabel, worthShowing } from "@web/usage/allowance";
import { paying } from "@web/billing/plan";
import {
    buyingHere, capWords, invoicePeriod, invoiceWord, isLocal, kindLabel, planPlace, priceOf, readCapTyped, seatWords,
    teamPowers, whoMayChange, type Buying, type Range,
} from "./words";

/** Usage and paying, yours and the organization's (PARITY.md, F): the web's
 *  `usage/usage-panel.tsx`, `billing/plan-section.tsx`, `org/org-usage.tsx`
 *  and `billing/team-billing.tsx`, a screen each. */

const LOCAL_SAYS = "A Lemma on your own computer has no plans and no billing: nothing is charged here, and nothing caps what is spent.";
const NONE_SAYS = "This server has no plans and no billing: nothing is charged here.";

/* ── your usage ──────────────────────────────────────────────────────── */

/** What you have spent, and what you may spend. Two questions on one screen,
 *  answered apart: the allowance is about the next run, the totals about the
 *  ones already made. Where no limit applies it says so, rather than drawing
 *  an empty meter of a limit that was never there. */
export function UsageScreen() {
    const { org, pending } = useOrg();
    return (
        <SettingsScreen title="Your usage" note="What you have spent, and what you may spend." back="Account">
            {pending ? <Reading /> : <YourUsage orgId={org?.id ?? null} />}
        </SettingsScreen>
    );
}

function YourUsage({ orgId }: { orgId: string | null }) {
    const [days, setDays] = useState<Range>(30);
    const scope = useMemo<SpendScope>(() => ({ whose: "mine", orgId }), [orgId]);
    const spend = useSpend(scope, days);
    const daily = useDaily(scope, days);
    return (
        <>
            <Allowance orgId={orgId} />
            <SpendGroup label="What you have spent" days={days} onDays={setDays} spend={spend} daily={daily} failed="Your usage could not be read." />
            <Slices label="By model" slices={spend.data?.byModel ?? []} max={6} />
        </>
    );
}

/** The windows that may stop your next run, each with its meter. */
function Allowance({ orgId }: { orgId: string | null }) {
    const limits = useMyLimits(orgId);
    const router = useRouter();
    const local = isLocal(useDeployment());
    const windows = limits.data?.windows ?? [];
    const place = planPlace(limits.data);
    /* Near the cap or past it, the way to raise it is one tap away. */
    const raise = !local && worthShowing(limits.data);
    return (
        <Group label={planLabel(limits.data)}>
            {limits.isPending ? <Reading label="Reading your allowance" /> : null}
            {limits.isError ? <ReadProblem said="Your allowance could not be read." onRetry={() => void limits.refetch()} /> : null}
            {limits.isSuccess && allowanceOf(limits.data).kind === "uncapped" ? (
                <Say>No spending limit applies to this account. Usage is still recorded below.</Say>
            ) : null}
            {windows.map((window, index) => (
                <WindowMeter key={window.key} window={window} limits={limits.data} last={index === windows.length - 1 && !raise} />
            ))}
            {raise ? (
                <Row
                    role="link"
                    label={place === "plan" ? "Your plan" : "The organization's billing"}
                    hint={place === "plan" ? "What it would take to spend more" : "The organization pays for this allowance"}
                    onPress={() => router.push(adminRoutes.section(place) as never)}
                    last
                />
            ) : null}
        </Group>
    );
}

/* ── your plan ───────────────────────────────────────────────────────── */

/** What you pay for, and what else is on offer. Yours only: an
 *  organization's plan is bought by the organization, on its own screen. */
export function PlanScreen() {
    const deployment = useDeployment();
    const { server } = useSession();
    const buying = buyingHere(Platform.OS, server.web, "mine");
    const none = useNoBilling();
    return (
        <SettingsScreen title="Your plan" note="What you pay for, and what it would take to be allowed to spend more." back="Account">
            {isLocal(deployment) ? <Say>{LOCAL_SAYS}</Say> : none ? <Say>{NONE_SAYS}</Say> : <YourPlan buying={buying} />}
        </SettingsScreen>
    );
}

function YourPlan({ buying }: { buying: Buying }) {
    const [waiting, setWaiting] = useState(false);
    const mine = useSubscription(null, waiting);
    const plans = usePlans();
    const start = useStart(null);
    const change = useChange(null);
    const cancel = useCancel(null);
    const settle = useSettle();
    /* Back from the browser: ask at once rather than at the next poll. */
    useOnReturn(() => { if (waiting) settle(); });
    const subscription = mine.data ?? null;
    const stoppable = buying.offer && paying(subscription) && !subscription?.cancel_at_period_end;
    return (
        <>
            <Current
                label="What you are on"
                subscription={subscription}
                pending={mine.isPending}
                failed={mine.isError ? problemOf(mine.error, "Your plan could not be read.") : null}
                onRetry={() => void mine.refetch()}
                none="Nothing is charged on this account. A plan raises the limits you work under."
            />
            {stoppable ? (
                <StopRenewing
                    name={subscription?.plan?.name ?? "your plan"}
                    body="Access runs to the end of the period you have paid for. It cannot be turned back on from here."
                    cancel={cancel}
                />
            ) : null}
            <Group label="Plans" foot={buying.elsewhere}>
                <PlanPicker
                    audience="PERSONAL"
                    plans={plans.data}
                    current={subscription}
                    pending={plans.isPending}
                    failed={plans.isError}
                    onRetry={() => void plans.refetch()}
                    start={(planId) => start.mutateAsync(planId)}
                    change={(planId) => change.mutateAsync(planId)}
                    onWaiting={setWaiting}
                    held={null}
                    buying={buying}
                />
            </Group>
        </>
    );
}

/* ── the organization's usage ────────────────────────────────────────── */

/** What the whole organization has spent: everybody's, not a wider view of
 *  yours. The server lets its owners and editors read it and refuses anybody
 *  else, so a refusal is an ordinary answer, said as one. */
export function OrgUsageScreen() {
    const { org, pending } = useOrg();
    return (
        <SettingsScreen title="Organization usage" note={org ? `Usage across ${org.name}.` : "Usage across this organization."} back="Account">
            {pending ? <Reading /> : org ? <OrgUsage org={org} /> : <Say>You are not in an organization, so there is nothing to add up here.</Say>}
        </SettingsScreen>
    );
}

function OrgUsage({ org }: { org: Org }) {
    const [days, setDays] = useState<Range>(30);
    const scope = useMemo<SpendScope>(() => ({ whose: "organization", orgId: org.id }), [org.id]);
    const spend = useSpend(scope, days);
    const daily = useDaily(scope, days);
    const contacts = useFeature("contacts");
    const router = useRouter();
    if (isForbidden(spend.error)) {
        return (
            <>
                <Say>Only owners and editors of {org.name} can see what it has spent.</Say>
                <Group>
                    <Row role="link" label="Your own usage" hint="What you have spent, and what you may spend" onPress={() => router.push(adminRoutes.section("usage") as never)} last />
                </Group>
            </>
        );
    }
    return (
        <>
            <SpendGroup label={`Spending across ${org.name}`} days={days} onDays={setDays} spend={spend} daily={daily} failed="The organization's usage could not be read." />
            {contacts ? <ContactsCapGroup orgId={org.id} /> : null}
            <Slices label="By model" slices={spend.data?.byModel ?? []} max={8} />
            <Slices label="By kind of work" slices={spend.data?.byKind ?? []} max={8} name={kindLabel} />
        </>
    );
}

/** What answering people outside the organization may cost it a month. An
 *  owner sets it; everyone who can read it sees it. */
function ContactsCapGroup({ orgId }: { orgId: string }) {
    const cap = useContactsCap(orgId);
    const save = useSetContactsCap(orgId);
    const me = useMyOrgRole(orgId);
    const [typed, setTyped] = useState<string | null>(null);
    const [problem, setProblem] = useState<string | null>(null);
    const [saved, setSaved] = useState(false);
    /* Nothing withheld on a guess: until the role is known the box is there,
       and the server refuses what it must. */
    const mayChange = !me.answered || me.role === "ORG_OWNER";
    const value = typed ?? (cap.data?.limit == null ? "" : String(cap.data.limit));
    const submit = () => {
        const read = readCapTyped(value);
        if ("problem" in read) {
            setProblem(read.problem);
            return;
        }
        setProblem(null);
        save.mutate(read.limit, {
            onSuccess: () => { setTyped(null); setSaved(true); },
            onError: (error) => setProblem(isForbidden(error) ? "Only an owner can change it." : problemOf(error, "That could not be saved. Try again.")),
        });
    };
    return (
        <Group
            label="Answering contacts"
            foot="Contacts are never billed: this is the most answering them may cost a month. Past it, the Lemmas here stop answering contacts until the month turns, and tell them a person will reply."
        >
            {cap.isPending ? <Reading label="Reading the cap" /> : null}
            {cap.isError ? (
                isForbidden(cap.error)
                    ? <Say>Only owners and editors see the contacts cap.</Say>
                    : <ReadProblem said="The contacts cap could not be read." onRetry={() => void cap.refetch()} />
            ) : null}
            {cap.data ? (
                <View style={{ gap: 12, paddingVertical: 6 }}>
                    <Counted size={15.5} style={{ lineHeight: 21 }}>{capWords(cap.data) + "."}</Counted>
                    {mayChange ? (
                        <>
                            <Field
                                label="Monthly cap, in dollars"
                                value={value}
                                onChange={(next) => { setTyped(next); setProblem(null); setSaved(false); }}
                                placeholder="No cap"
                                keyboardType="decimal-pad"
                                error={problem}
                                hint="Leave it empty for no cap."
                                onSubmit={submit}
                            />
                            <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                                <Button title={save.isPending ? "Saving…" : "Save"} busy={save.isPending} disabled={typed === null} onPress={submit} />
                                {saved ? <Say tone="done">Saved.</Say> : null}
                            </View>
                        </>
                    ) : (
                        <Say>Only an owner changes it.</Say>
                    )}
                </View>
            ) : null}
        </Group>
    );
}

/* ── the organization's billing ──────────────────────────────────────── */

/** What the organization pays for, its seats, its invoices, and who may
 *  change it. Three permissions on one screen, as the platform holds them:
 *  anyone in it reads the plan and the seats, an owner or an editor buys,
 *  switches and reads the invoices, and only an owner stops it. A member
 *  still sees the plans: one you cannot buy is one you can ask for. */
export function TeamBillingScreen() {
    const { org, pending } = useOrg();
    const deployment = useDeployment();
    const { server } = useSession();
    const buying = buyingHere(Platform.OS, server.web, "organization");
    const none = useNoBilling();
    const note = `What ${org?.name ?? "this organization"} pays for, how many seats it has bought, and who may change that.`;
    return (
        <SettingsScreen title="Billing" note={note} back="Account">
            {isLocal(deployment)
                ? <Say>{LOCAL_SAYS}</Say>
                : none
                    ? <Say>{NONE_SAYS}</Say>
                : pending
                    ? <Reading />
                    : org
                        ? <TeamBilling org={org} buying={buying} />
                        : <Say>You are not in an organization, so there is nothing to bill.</Say>}
        </SettingsScreen>
    );
}

function TeamBilling({ org, buying }: { org: Org; buying: Buying }) {
    const me = useMyOrgRole(org.id);
    const powers = teamPowers(me.role, me.answered);
    const [waiting, setWaiting] = useState(false);
    const plan = useSubscription(org.id, waiting);
    const plans = usePlans();
    const seats = useSeats(org.id);
    const invoices = useInvoices(org.id, powers.seesInvoices);
    const start = useStart(org.id);
    const change = useChange(org.id);
    const cancel = useCancel(org.id);
    const settle = useSettle();
    /* An invoice opened to pay, or a plan being paid for: read again on the
       way back from the browser. */
    const paid = useRef(false);
    useOnReturn(() => {
        if (waiting || paid.current) settle();
        paid.current = false;
    });

    if (isForbidden(plan.error)) return <Say>Only members of {org.name} can see what it pays for.</Say>;

    const subscription = plan.data ?? null;
    const stoppable = buying.offer && powers.mayStop && paying(subscription) && !subscription?.cancel_at_period_end;
    const seatSaid = seats.data ? seatWords(seats.data, paying(subscription)) : null;
    const showInvoices = powers.seesInvoices && (invoices.isPending || invoices.isError || (invoices.data?.length ?? 0) > 0);
    return (
        <>
            <Current
                label={`What ${org.name} is on`}
                subscription={subscription}
                pending={plan.isPending}
                failed={plan.isError ? problemOf(plan.error, "The plan could not be read.") : null}
                onRetry={() => void plan.refetch()}
                none="This organization has not bought a plan."
            />
            {stoppable ? (
                <StopRenewing
                    name={subscription?.plan?.name ?? "the plan"}
                    body="Everyone here keeps working until the end of the period already paid for. It cannot be turned back on from here."
                    cancel={cancel}
                />
            ) : null}

            <Group label="Seats">
                {seats.isPending ? <Reading label="Reading the seats" /> : null}
                {seats.isError ? <ReadProblem said={problemOf(seats.error, "The seats could not be read.")} onRetry={() => void seats.refetch()} /> : null}
                {seatSaid ? (
                    <>
                        <Figure label="Paid for" value={seatSaid.bought} />
                        <Figure label={`People in ${org.name}`} value={seatSaid.present} last />
                        {seatSaid.short ? (
                            <Counted size={14} tone={color.amber} style={{ lineHeight: 20, paddingTop: 4 }}>
                                {`${seatSaid.short} Nothing is charged for them until the plan next changes.`}
                            </Counted>
                        ) : null}
                    </>
                ) : null}
            </Group>

            <Group label="Plans" foot={powers.held ?? buying.elsewhere}>
                <PlanPicker
                    audience="TEAM"
                    plans={plans.data}
                    current={subscription}
                    pending={plans.isPending}
                    failed={plans.isError}
                    onRetry={() => void plans.refetch()}
                    start={(planId) => start.mutateAsync(planId)}
                    change={(planId) => change.mutateAsync(planId)}
                    onWaiting={setWaiting}
                    held={powers.held}
                    buying={buying}
                />
            </Group>

            {showInvoices ? (
                <Group label="Invoices">
                    {invoices.isPending ? <Reading label="Reading the invoices" /> : null}
                    {invoices.isError ? <ReadProblem said={problemOf(invoices.error, "The invoices could not be read.")} onRetry={() => void invoices.refetch()} /> : null}
                    {(invoices.data ?? []).map((invoice, index, all) => {
                        const period = invoicePeriod(invoice);
                        const owed = invoice.status === "unpaid" || invoice.status === "failed";
                        const payAt = buying.offer && invoice.status === "unpaid" && invoice.checkout_url ? invoice.checkout_url : null;
                        return (
                            <Figure
                                key={invoice.id}
                                label={period}
                                hint={invoiceWord(invoice.status)}
                                hintTone={owed ? color.amber : color.ink3}
                                value={priceOf(invoice.total_cents, invoice.currency)}
                                end={payAt ? <EndPress title="Pay" label={`Pay the invoice for ${period}`} onPress={() => { paid.current = true; void openOutside(payAt); }} /> : null}
                                last={index === all.length - 1}
                            />
                        );
                    })}
                </Group>
            ) : null}

            <Group label="Who may change it">
                <Say>{whoMayChange(me.role)}</Say>
            </Group>
        </>
    );
}
