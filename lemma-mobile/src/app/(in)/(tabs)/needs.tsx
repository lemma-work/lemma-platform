import { useMemo, useState } from "react";
import { RefreshControl, ScrollView, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLemmas, useNeeds, usePulse } from "../../../data/hooks";
import { FILTER_LABELS, FILTER_LINES, filterOf, needGroups, type NeedFilter } from "../../../data/model";
import { SAMPLE_SHORT } from "../../../data/sample";
import type { NeedItem } from "../../../data/types";
import { useSession } from "../../../session/session";
import { Loading } from "../../../ui/chrome";
import { Calm } from "../../../ui/calm";
import { NeedRow } from "../../../ui/need-card";
import { Press } from "../../../ui/press";
import { MeButton } from "../../../ui/me-button";
import { Num, Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { FormSheet } from "../../../areas/work/wait-form";

const FILTERS: NeedFilter[] = ["all", "decide", "answer", "do"];
const GROUPS = ["decide", "answer", "do"] as const;

/** Needs you, as the web's page (`outcomes/needs.tsx`): how many, across how
 *  many Lemmas; chips to narrow it, zero counts hidden; then Decide, Answer
 *  and Do, each answered right in its row. A workflow's form opens in a
 *  sheet (area D's `FormSheet`). */
export default function Needs() {
    const { mode } = useSession();
    const { needs, pending } = useNeeds();
    const { refetch, fetching } = usePulse();
    const { lemmas } = useLemmas();
    const [filter, setFilter] = useState<NeedFilter>("all");
    const [form, setForm] = useState<NeedItem | null>(null);
    /* Forms sent from here, gone from the list before the queue is read again. */
    const [sent, setSent] = useState<ReadonlySet<string>>(new Set());
    const now = Date.now();
    const open = useMemo(() => needs.filter((item) => !sent.has(item.key)), [needs, sent]);
    const counts = useMemo(() => {
        const out: Record<NeedFilter, number> = { all: open.length, decide: 0, answer: 0, do: 0 };
        for (const item of open) out[filterOf(item.kind)] += 1;
        return out;
    }, [open]);
    const shown = filter === "all" ? open : open.filter((item) => filterOf(item.kind) === filter);
    const groups = needGroups(shown);
    const across = new Set(open.map((item) => item.podId)).size;
    const line = open.length === 0
        ? "Nothing waits on you."
        : `${open.length} ${open.length === 1 ? "thing" : "things"} across ${across} of ${lemmas.length} ${lemmas.length === 1 ? "Lemma" : "Lemmas"}, oldest first.`;
    const titleOf = (item: NeedItem) => (mode === "sample" ? SAMPLE_SHORT[item.key] ?? item.title : item.title);

    return (
        <SafeAreaView edges={["top"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <View style={{ flexDirection: "row", justifyContent: "flex-end", paddingTop: 6, paddingHorizontal: 14 }}>
                <MeButton />
            </View>
            <ScrollView
                stickyHeaderIndices={[1]}
                contentContainerStyle={{ paddingBottom: 32 }}
                refreshControl={<RefreshControl refreshing={fetching && !pending} onRefresh={() => void refetch()} tintColor={color.ink3} />}
            >
                <View style={{ paddingHorizontal: 20, paddingBottom: 14 }}>
                    <Text accessibilityRole="header" size={30} weight="medium" style={{ letterSpacing: -0.6, lineHeight: 35 }}>Needs you</Text>
                    {pending ? null : <Text size={14.5} tone={color.ink2} style={{ marginTop: 4 }}>{line}</Text>}
                </View>
                <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ backgroundColor: color.paper }} contentContainerStyle={{ gap: 8, paddingHorizontal: 16, paddingBottom: 6 }}>
                    {FILTERS.filter((one) => one === "all" || counts[one] > 0).map((one) => {
                        const on = filter === one;
                        return (
                            <Press
                                key={one}
                                label={`${FILTER_LABELS[one]}, ${counts[one]}`}
                                accessibilityState={{ selected: on }}
                                onPress={() => setFilter(one)}
                                style={{ height: 34, paddingHorizontal: 13, borderRadius: 999, borderWidth: 1, borderColor: on ? color.info : color.line2, backgroundColor: on ? color.pick : color.paper, flexDirection: "row", alignItems: "center", gap: 6 }}
                            >
                                <Text size={14} tone={on ? color.info : color.ink}>{FILTER_LABELS[one]}</Text>
                                <Num size={12.5} tone={on ? color.info : color.ink3}>{counts[one]}</Num>
                            </Press>
                        );
                    })}
                </ScrollView>

                {pending ? <View style={{ padding: 20 }}><Loading label="Reading what needs you" /></View> : null}
                {!pending && open.length === 0 ? (
                    <Calm faces={lemmas.slice(0, 3)} title="Nothing needs you" line="When a Lemma needs your yes, an answer or a form, it waits here." />
                ) : null}
                {GROUPS.map((group) => groups[group].length === 0 ? null : (
                    <View key={group} style={{ marginTop: 14 }}>
                        <View style={{ flexDirection: "row", alignItems: "baseline", gap: 8, paddingHorizontal: 20, paddingBottom: 4 }}>
                            <Text size={15.5} weight="medium">{FILTER_LABELS[group]}</Text>
                            <Text size={13} tone={color.ink3}>{FILTER_LINES[group]}</Text>
                        </View>
                        {groups[group].map((item, index) => (
                            <NeedRow key={item.key} item={item} lemma={lemmas.find((one) => one.id === item.podId) ?? null} now={now} title={titleOf(item)} onForm={setForm} first={index === 0} />
                        ))}
                    </View>
                ))}
                {open.length > 0 ? (
                    <Text size={13} tone={color.ink3} style={{ paddingHorizontal: 20, paddingTop: 18 }}>
                        Answered here or where it was asked, it leaves this list either way.
                    </Text>
                ) : null}
            </ScrollView>
            {form?.workflowRunId ? (
                <FormSheet
                    podId={form.podId}
                    runId={form.workflowRunId}
                    title={titleOf(form)}
                    workflow={form.detail}
                    open
                    onClose={() => setForm(null)}
                    onSent={() => { setSent((was) => new Set(was).add(form.key)); setForm(null); }}
                />
            ) : null}
        </SafeAreaView>
    );
}
