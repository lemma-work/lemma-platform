import { useEffect, useState } from "react";
import { ActivityIndicator, ScrollView, View } from "react-native";
import { Icon } from "../../../ui/icons";
import { Button, Press } from "../../../ui/press";
import { Sheet } from "../../../ui/sheet";
import { Counted, Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { openOutside } from "../connect/round-trip";
import { ReadProblem, Reading, Say, problemOf } from "../ui";
import { Allows } from "./parts";
import { highlights, moveTo, offered as offeredPlans, type ChangeAcknowledged, type Plan, type PlanAudience, type StartedCheckout, type Subscription } from "@web/billing/plan";
import { afterStart, priceWords, type Buying } from "./words";

/** Choosing a plan, and the wait that follows (the web's `billing/plan-picker.tsx`).
 *
 *  The plans on offer are a list; one opens in a sheet that says what it
 *  allows and, where this phone offers buying, the way to choose it. Nothing
 *  about a plan changes until the provider says the payment landed, so a
 *  checkout opens in the phone's browser and this screen keeps asking
 *  (`hooks.ts` `useSubscription` with `watch`), and asks again the moment the
 *  person is back. A move between paid plans needs no checkout: the card on
 *  file settles it, and the screen waits the same way.
 *
 *  On iOS nothing here buys (`words.ts` `buyingHere`): the plans are shown,
 *  each says where to change it, and no button or link leaves the app. */
export function PlanPicker({ audience, plans, current, pending, failed, onRetry, start, change, onWaiting, held, buying }: {
    audience: PlanAudience;
    plans: Plan[] | undefined;
    current: Subscription | null | undefined;
    /** The plans are still being read, so no plans reads as loading. */
    pending: boolean;
    failed: boolean;
    onRetry: () => void;
    start: (planId: string) => Promise<StartedCheckout>;
    change: (planId: string) => Promise<ChangeAcknowledged>;
    /** Turns the payer's poll on and off. */
    onWaiting: (waiting: boolean) => void;
    /** Why this person may look but not buy (a member of the organization),
     *  or null. A plan you cannot choose is still one you can ask for. */
    held: string | null;
    buying: Buying;
}) {
    const [open, setOpen] = useState<Plan | null>(null);
    const [chosen, setChosen] = useState<string | null>(null);
    const [checkout, setCheckout] = useState<string | null>(null);
    const [asked, setAsked] = useState(false);
    const [busy, setBusy] = useState<string | null>(null);
    const [problem, setProblem] = useState<string | null>(null);
    const [done, setDone] = useState<string | null>(null);

    const offered = offeredPlans(plans, audience);
    const settled = chosen !== null && current?.plan_id === chosen;

    /* The payment landed: what was waiting is over, and the list is a list
       of plans again, with the new one ticked. */
    useEffect(() => {
        if (!settled) return;
        const name = offered.find((plan) => plan.id === chosen)?.name ?? current?.plan?.name ?? "the new plan";
        setDone(`You are on ${name} now.`);
        setChosen(null);
        setCheckout(null);
        setAsked(false);
        onWaiting(false);
    }, [settled, chosen, offered, current, onWaiting]);

    const stopWaiting = () => {
        setChosen(null);
        setCheckout(null);
        setAsked(false);
        onWaiting(false);
    };

    async function pick(plan: Plan) {
        const move = moveTo(current, plan);
        if (move === "current" || move === "contact" || !buying.offer || held) return;
        setBusy(plan.id);
        setProblem(null);
        setDone(null);
        try {
            if (move === "change") {
                await change(plan.id);
                setChosen(plan.id);
                setAsked(true);
                onWaiting(true);
                return;
            }
            const next = afterStart(await start(plan.id));
            if (next.kind === "failed") {
                setProblem(next.said);
                return;
            }
            setChosen(plan.id);
            if (next.kind === "open") {
                setCheckout(next.url);
                onWaiting(true);
                await openOutside(next.url).catch(() => setProblem("The payment page could not be opened. Open the payment again to try once more."));
            }
        } catch (failure) {
            setProblem(problemOf(failure, "That could not be started. Nothing has been charged."));
        } finally {
            setBusy(null);
        }
    }

    return (
        <View>
            {checkout || asked ? (
                <View accessibilityLiveRegion="polite" style={{ gap: 10, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: color.line }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                        <ActivityIndicator size="small" color={color.violet} />
                        <Text size={15.5}>Waiting for the payment…</Text>
                    </View>
                    <Say>
                        {checkout
                            ? "The payment happens at our provider, in your browser. Nothing changes here until it goes through, and this screen notices when it does."
                            : "Asked for. The card on file settles the difference, and the plan moves once the payment does."}
                    </Say>
                    <View style={{ flexDirection: "row", gap: 10 }}>
                        {checkout ? <Button title="Open the payment again" kind="outline" onPress={() => void openOutside(checkout)} /> : null}
                        {/* Stops the asking; cancels nothing, since an abandoned
                            checkout writes nothing at either end. */}
                        <Button title="Not now" kind="quiet" onPress={stopWaiting} />
                    </View>
                </View>
            ) : null}
            {problem ? <View style={{ paddingVertical: 6 }}><Say tone="bad">{problem}</Say></View> : null}
            {done ? <View style={{ paddingVertical: 6 }}><Say tone="done">{done}</Say></View> : null}
            {pending && offered.length === 0 ? <Reading label="Reading the plans" /> : null}
            {failed ? <ReadProblem said="The plans could not be read." onRetry={onRetry} /> : null}
            {!pending && !failed && offered.length === 0 ? <Say>No plans are on offer here.</Say> : null}
            {offered.map((plan, index) => {
                const here = moveTo(current, plan) === "current";
                const price = priceWords(plan);
                return (
                    <Press
                        key={plan.id}
                        label={`${plan.name}, ${price}${here ? ", your plan now" : ""}`}
                        onPress={() => setOpen(plan)}
                        style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 58, paddingVertical: 6, borderBottomWidth: index === offered.length - 1 ? 0 : 1, borderBottomColor: color.line }}
                    >
                        <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
                            <Text size={16} numberOfLines={1}>{plan.name}</Text>
                            <Counted size={13.5} tone={color.ink3} numberOfLines={1}>{price}</Counted>
                        </View>
                        {here ? <Text size={14.5} tone={color.ink3}>Current</Text> : null}
                        {busy === plan.id ? <ActivityIndicator size="small" color={color.ink3} /> : <Icon name="forward" size={16} tone={color.ink3} />}
                    </Press>
                );
            })}
            <PlanSheet
                plan={open}
                current={current}
                held={held}
                buying={buying}
                waiting={chosen !== null || busy !== null}
                onClose={() => setOpen(null)}
                onPick={(plan) => { setOpen(null); void pick(plan); }}
            />
        </View>
    );
}

/** One plan: its price, what it is for and allows, and what can be done
 *  about it from here. */
function PlanSheet({ plan, current, held, buying, waiting, onClose, onPick }: {
    plan: Plan | null;
    current: Subscription | null | undefined;
    held: string | null;
    buying: Buying;
    waiting: boolean;
    onClose: () => void;
    onPick: (plan: Plan) => void;
}) {
    const move = plan ? moveTo(current, plan) : "current";
    return (
        <Sheet open={plan !== null} onClose={onClose} tall>
            {plan ? (
                <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 8, gap: 12 }}>
                    <View style={{ gap: 4 }}>
                        <Text size={20} weight="medium">{plan.name}</Text>
                        <Counted size={15.5} tone={color.ink2}>{priceWords(plan)}</Counted>
                    </View>
                    {plan.description ? <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20 }}>{plan.description}</Text> : null}
                    {highlights(plan).length > 0 ? <Allows lines={highlights(plan)} /> : null}
                    <View style={{ gap: 10, paddingTop: 4 }}>
                        {move === "current" ? (
                            <Say>You are on this plan.</Say>
                        ) : move === "contact" ? (
                            <Say>This plan is bought by talking to us.</Say>
                        ) : !buying.offer ? (
                            <Say>{buying.elsewhere}</Say>
                        ) : held ? (
                            <Say>{held}</Say>
                        ) : (
                            <>
                                <Say>
                                    {move === "change"
                                        ? "The card on file pays the difference, and the plan moves once the payment does."
                                        : "You pay at our payment provider, in your browser. Nothing changes here until the payment goes through."}
                                </Say>
                                <Button title={move === "change" ? "Switch to this" : "Choose"} disabled={waiting} height={46} onPress={() => onPick(plan)} />
                            </>
                        )}
                    </View>
                </ScrollView>
            ) : null}
        </Sheet>
    );
}
