import { useState, type ReactNode } from "react";
import { View } from "react-native";
import type { MyUsageLimitsResponse, UsageAllowanceResponse } from "lemma-sdk";
import { Meter } from "../../../ui/chrome";
import { Icon } from "../../../ui/icons";
import { Button, Press } from "../../../ui/press";
import { Counted, Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { Confirm, Group, ReadProblem, Reading, Say, isForbidden, problemOf } from "../ui";
import type { DaySpend, Spend } from "./source";
import { formatCost, formatPercent, type Slice } from "@web/usage/allowance";
import { highlights, type Subscription } from "@web/billing/plan";
import { RANGES, busiestWords, currentWords, dayBars, meterFill, tokens, windowNote, windowTone, type Range } from "./words";

/** The pieces the usage and billing screens share: a figure with its number
 *  in Inter, a window's meter, the days to read over, a bar per day, where
 *  the money went, what a payer is on, and the way to stop renewing. */

/** One figure: what it is, and its number at the end. `hint` is a quieter
 *  second line; `end` a control after the number (Pay). */
export function Figure({ label, value, hint, hintTone, end, last = false }: {
    label: string;
    value: string;
    hint?: string | null;
    hintTone?: string;
    end?: ReactNode;
    last?: boolean;
}) {
    return (
        <View
            accessible={!end}
            accessibilityLabel={`${label}, ${value}${hint ? `, ${hint}` : ""}`}
            style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: hint ? 58 : 50, paddingVertical: 6, borderBottomWidth: last ? 0 : 1, borderBottomColor: color.line }}
        >
            <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
                <Text size={16} numberOfLines={1}>{label}</Text>
                {hint ? <Text size={13} tone={hintTone ?? color.ink3} numberOfLines={2}>{hint}</Text> : null}
            </View>
            <Counted size={15} tone={color.ink2} numberOfLines={1}>{value}</Counted>
            {end}
        </View>
    );
}

/** A window of the allowance: its name, how much is used, the meter, and
 *  when it starts over. Amber near the cap and once spent. */
export function WindowMeter({ window, limits, last = false }: { window: UsageAllowanceResponse; limits: MyUsageLimitsResponse | undefined; last?: boolean }) {
    const tone = windowTone(window, limits);
    const ink = tone === "quiet" ? color.ink2 : color.amber;
    const much = formatPercent(window.used_percent, window.allowed);
    const note = windowNote(window);
    return (
        <View style={{ gap: 6, paddingVertical: 10, borderBottomWidth: last ? 0 : 1, borderBottomColor: color.line }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
                <Text size={16} style={{ flex: 1 }} numberOfLines={1}>{window.label}</Text>
                <Counted size={15} tone={ink}>{much}</Counted>
            </View>
            <Meter fill={meterFill(window.used_percent)} mark={null} label={`${window.label}: ${much} used${note ? `. ${note}` : ""}`} />
            {note ? <Counted size={13} tone={tone === "blocked" ? color.amber : color.ink3}>{note}</Counted> : null}
        </View>
    );
}

/** The days spending is read over: three choices, the one held filled. */
export function RangePicker({ days, onPick }: { days: Range; onPick: (days: Range) => void }) {
    return (
        <View accessibilityRole="radiogroup" accessibilityLabel="How far back" style={{ flexDirection: "row", gap: 8, paddingVertical: 8 }}>
            {RANGES.map((range) => {
                const on = range === days;
                return (
                    <Press
                        key={range}
                        role="radio"
                        label={`The last ${range} days`}
                        accessibilityState={{ selected: on }}
                        onPress={() => onPick(range)}
                        style={{ height: 34, paddingHorizontal: 13, borderRadius: 999, justifyContent: "center", backgroundColor: on ? color.ink : color.paper, borderWidth: on ? 0 : 1, borderColor: color.line2 }}
                    >
                        <Counted size={14} tone={on ? color.onInk : color.ink}>{`${range} days`}</Counted>
                    </Press>
                );
            })}
        </View>
    );
}

/** One bar per day, scaled to the busiest, which is drawn darkest and named
 *  under them. */
export function DayBars({ days }: { days: readonly DaySpend[] }) {
    const { heights, busiest } = dayBars(days);
    if (!busiest) return null;
    const said = busiestWords(busiest);
    return (
        <View style={{ gap: 6, paddingTop: 12, paddingBottom: 4 }}>
            <View
                accessible
                accessibilityRole="image"
                accessibilityLabel={`Spending by day. ${said}`}
                style={{ height: 64, flexDirection: "row", alignItems: "flex-end", gap: days.length > 40 ? 1 : 2 }}
            >
                {heights.map((height, index) => (
                    <View
                        key={days[index].day}
                        style={{ flex: 1, height: `${Math.round(height * 1000) / 10}%`, borderTopLeftRadius: 2, borderTopRightRadius: 2, backgroundColor: days[index] === busiest ? color.ink : color.ink3 }}
                    />
                ))}
            </View>
            <Counted size={13} tone={color.ink3}>{said}</Counted>
        </View>
    );
}

/** What was spent over the chosen days: the range, the totals, a bar per
 *  day. `failed` is what a failed read says. */
export function SpendGroup({ label, days, onDays, spend, daily, failed }: {
    label: string;
    days: Range;
    onDays: (days: Range) => void;
    spend: { data: Spend | undefined; isPending: boolean; isError: boolean; error: unknown; refetch: () => unknown };
    daily: { data: DaySpend[] | undefined };
    failed: string;
}) {
    return (
        <Group label={label}>
            <RangePicker days={days} onPick={onDays} />
            {spend.isPending ? <Reading label="Adding it up" /> : null}
            {spend.isError ? <ReadProblem said={problemOf(spend.error, failed)} onRetry={() => void spend.refetch()} /> : null}
            {spend.data ? (
                <>
                    <Figure label="Cost" value={formatCost(spend.data.cost)} />
                    <Figure label="Tokens in" value={tokens(spend.data.tokensIn)} />
                    <Figure label="Tokens out" value={tokens(spend.data.tokensOut)} last={!daily.data?.length} />
                    {daily.data ? <DayBars days={daily.data} /> : null}
                    {spend.data.tokens === 0 ? <Counted size={14} tone={color.ink2} style={{ lineHeight: 20 }}>{`Nothing was recorded in the last ${days} days.`}</Counted> : null}
                </>
            ) : null}
        </Group>
    );
}

/** Where the money went, most first. A model with no price says so: $0.00
 *  would call it free. */
export function Slices({ label, slices, max, name = (said) => said }: { label: string; slices: readonly Slice[]; max: number; name?: (label: string) => string }) {
    if (slices.length === 0) return null;
    const shown = slices.slice(0, max);
    return (
        <Group label={label}>
            {shown.map((slice, index) => (
                <Figure key={slice.label} label={name(slice.label)} value={slice.cost === null ? "Not priced" : formatCost(slice.cost)} last={index === shown.length - 1} />
            ))}
        </Group>
    );
}

/** What a payer is on: the plan's name and price, when it renews, anything
 *  wrong with it, and what it allows. */
export function Current({ label, subscription, pending, failed, onRetry, none }: {
    label: string;
    subscription: Subscription | null | undefined;
    pending: boolean;
    failed: string | null;
    onRetry: () => void;
    /** What is said when nothing is charged. */
    none: string;
}) {
    const words = currentWords(subscription, none);
    const allows = subscription?.plan ? highlights(subscription.plan) : [];
    return (
        <Group label={label}>
            {pending ? <Reading label="Reading the plan" /> : null}
            {failed ? <ReadProblem said={failed} onRetry={onRetry} /> : null}
            {!pending && !failed ? (
                <View style={{ gap: 4, paddingVertical: 8 }}>
                    <Text size={19} weight="medium">{words.name}</Text>
                    <Counted size={14.5} tone={color.ink2} style={{ lineHeight: 20 }}>{words.price}</Counted>
                    {words.renews ? <Counted size={13.5} tone={color.ink3}>{words.renews}</Counted> : null}
                </View>
            ) : null}
            {words.note ? <Say tone={words.trouble ? "bad" : "quiet"}>{words.note}</Say> : null}
            {!pending && !failed && allows.length > 0 ? <Allows lines={allows} /> : null}
        </Group>
    );
}

/** A plan's highlights, a tick before each. */
export function Allows({ lines }: { lines: readonly string[] }) {
    return (
        <View style={{ gap: 6, paddingTop: 4 }}>
            {lines.map((said) => (
                <View key={said} style={{ flexDirection: "row", gap: 8, alignItems: "flex-start" }}>
                    <View style={{ paddingTop: 2 }}><Icon name="check" size={15} tone={color.ink2} width={2} /></View>
                    <Counted size={14.5} tone={color.ink2} style={{ flex: 1, lineHeight: 20 }}>{said}</Counted>
                </View>
            ))}
        </View>
    );
}

/** Stop renewing, after a confirmation that says what follows. There is no
 *  call that takes it back, so it is asked first, and said plainly. */
export function StopRenewing({ name, body, cancel }: {
    name: string;
    body: string;
    cancel: { mutate: (vars: undefined, options: { onSuccess: () => void }) => void; isPending: boolean; isError: boolean; error: unknown; reset: () => void };
}) {
    const [asking, setAsking] = useState(false);
    return (
        <View style={{ flexDirection: "row", marginTop: -8 }}>
            <Button title="Stop renewing" kind="outline" onPress={() => { cancel.reset(); setAsking(true); }} />
            <Confirm
                open={asking}
                title={`Stop renewing ${name}?`}
                body={body}
                confirm={cancel.isPending ? "Stopping…" : "Stop renewing"}
                busy={cancel.isPending}
                error={cancel.isError ? (isForbidden(cancel.error) ? "Only an owner can stop it." : "That could not be stopped. Nothing has changed.") : null}
                onClose={() => { if (!cancel.isPending) setAsking(false); }}
                onConfirm={() => cancel.mutate(undefined, { onSuccess: () => setAsking(false) })}
            />
        </View>
    );
}

/** A quiet press at the end of a figure: Pay. */
export function EndPress({ title, label, onPress }: { title: string; label: string; onPress: () => void }) {
    return (
        <Press label={label} onPress={onPress} style={{ minHeight: 44, justifyContent: "center", paddingLeft: 6 }}>
            <Text size={14.5} weight="medium">{title}</Text>
        </Press>
    );
}
