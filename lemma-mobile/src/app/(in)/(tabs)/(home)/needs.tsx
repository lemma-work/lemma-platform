import { useMemo, useState } from "react";
import { RefreshControl, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLemmas, useNeeds, useOwnsAll, usePulse } from "../../../../data/hooks";
import { FILTER_LABELS, ageOf, filterOf, hrefOfNeed, needGroups, type NeedFilter } from "../../../../data/model";
import { SAMPLE_SHORT } from "../../../../data/sample";
import type { NeedItem } from "../../../../data/types";
import { useSession } from "../../../../session/session";
import { BackLink, Empty, Loading } from "../../../../ui/chrome";
import { LemmaFace, PersonFace } from "../../../../ui/face";
import { Button, Press } from "../../../../ui/press";
import { Counted, Num, Text } from "../../../../ui/text";
import { color } from "../../../../ui/tokens";
import { FormSheet } from "../../../../areas/work/wait-form";

const FILTERS: NeedFilter[] = ["all", "yes", "questions", "people", "forms"];

/** Everything that needs you: chips to narrow it, then one line each,
 *  behind first, then today, then earlier. A contact waiting on a person is
 *  "Reply to {name}", with their initials rather than a Lemma's face. A
 *  workflow's form is answered here, in a sheet (area D's `FormSheet`). */
export default function Needs() {
    const router = useRouter();
    const { mode } = useSession();
    const { needs, pending } = useNeeds();
    const { refetch, fetching } = usePulse();
    const { lemmas } = useLemmas();
    const owns = useOwnsAll(lemmas);
    const [filter, setFilter] = useState<NeedFilter>("all");
    const [form, setForm] = useState<NeedItem | null>(null);
    /* Forms sent from here, gone from the list before the queue is read again. */
    const [sent, setSent] = useState<ReadonlySet<string>>(new Set());
    const now = Date.now();
    const behind = useMemo(() => new Set(lemmas.filter((one) => owns.get(one.id)?.standing === "behind").map((one) => one.id)), [lemmas, owns]);
    const open = useMemo(() => needs.filter((item) => !sent.has(item.key)), [needs, sent]);
    const counts = useMemo(() => {
        const out: Record<NeedFilter, number> = { all: open.length, yes: 0, questions: 0, people: 0, forms: 0 };
        for (const item of open) out[filterOf(item.kind)] += 1;
        return out;
    }, [open]);
    const shown = filter === "all" ? open : open.filter((item) => filterOf(item.kind) === filter);
    const groups = needGroups(shown, behind, now);
    const [ruleSeen, setRuleSeen] = useState(false);
    const iconOf = (podId: string) => lemmas.find((one) => one.id === podId)?.iconUrl ?? null;

    const row = (item: NeedItem, late: boolean) => {
        const title = mode === "sample" ? SAMPLE_SHORT[item.key] ?? item.title : item.title;
        return (
            <Press
                key={item.key}
                role="link"
                label={`${item.podName}: ${title}, ${ageOf(item.sinceMs, now)}`}
                onPress={() => (item.kind === "FORM" && item.workflowRunId ? setForm(item) : router.push(hrefOfNeed(item) as never))}
                style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: 48, paddingHorizontal: 4, borderBottomWidth: 1, borderBottomColor: color.line }}
            >
                {item.kind === "CONTACT" ? <PersonFace name={item.title.replace(/^Reply to /, "")} size={26} /> : <LemmaFace id={item.podId} icon={iconOf(item.podId)} size={26} />}
                <Counted size={15.5} numberOfLines={1} style={{ flex: 1, minWidth: 0 }}>{title}</Counted>
                <Num size={13} tone={late ? color.amber : color.ink3}>{ageOf(item.sinceMs, now)}</Num>
            </Press>
        );
    };

    const heading = (text: string, late = false, first = false) => (
        <Text size={12.5} tone={late ? color.amber : color.ink3} style={{ marginTop: first ? 4 : 16, marginBottom: 4, marginHorizontal: 4 }}>{text}</Text>
    );

    return (
        <SafeAreaView edges={["top"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <View style={{ paddingTop: 4, paddingHorizontal: 6 }}>
                <View style={{ alignSelf: "flex-start" }}><BackLink label="Home" /></View>
            </View>
            <View style={{ paddingHorizontal: 20, paddingBottom: 10 }}>
                <Text accessibilityRole="header" size={28} weight="medium" style={{ letterSpacing: -0.56, lineHeight: 32 }}>
                    Needs you <Num size={28} tone={color.ink3}>{open.length}</Num>
                </Text>
            </View>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ flexGrow: 0 }} contentContainerStyle={{ gap: 6, paddingHorizontal: 16, paddingTop: 2, paddingBottom: 12 }}>
                {FILTERS.filter((one) => one === "all" || counts[one] > 0).map((one) => {
                    const on = filter === one;
                    return (
                        <Press
                            key={one}
                            label={`${FILTER_LABELS[one]}${one === "all" ? "" : `, ${counts[one]}`}`}
                            accessibilityState={{ selected: on }}
                            onPress={() => setFilter(one)}
                            hitSlop={{ top: 6, bottom: 6 }}
                            style={{ height: 32, paddingHorizontal: 12, borderRadius: 999, backgroundColor: on ? color.ink : color.paper, borderWidth: on ? 0 : 1, borderColor: color.line2, flexDirection: "row", alignItems: "center", gap: 4 }}
                        >
                            <Text size={13.5} tone={on ? color.onInk : color.ink}>{FILTER_LABELS[one]}</Text>
                            {one === "all" ? null : <Num size={13.5} tone={on ? color.onInk : color.ink}>{counts[one]}</Num>}
                        </Press>
                    );
                })}
            </ScrollView>
            <ScrollView
                contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 24 }}
                refreshControl={<RefreshControl refreshing={fetching && !pending} onRefresh={() => void refetch()} tintColor={color.ink3} />}
            >
                {mode === "sample" && !ruleSeen && filter === "all" ? (
                    /* A rule suggestion, as the canvas draws it. Nothing on the
                       server suggests rules yet, so only sample data shows one. */
                    <View style={{ backgroundColor: color.violetWash, borderRadius: 16, padding: 14, gap: 10, marginBottom: 14 }}>
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                            <LemmaFace id={lemmas[0]?.id ?? "sample"} size={22} />
                            <Text size={13} weight="medium" tone={color.violetInk}>{lemmas[0]?.name ?? "Support"} suggests a rule</Text>
                        </View>
                        <Counted size={15.5} style={{ lineHeight: 21.4 }}>You said yes to all 6 refunds under $300 this month. Refund those without asking you?</Counted>
                        <View style={{ flexDirection: "row", gap: 8 }}>
                            <Button title="Make it a rule" height={38} size={14} onPress={() => setRuleSeen(true)} />
                            <Button title="Keep asking" kind="outline" height={38} size={14} onPress={() => setRuleSeen(true)} />
                        </View>
                    </View>
                ) : null}
                {pending ? <Loading /> : null}
                {!pending && shown.length === 0 ? <Empty title="Nothing needs you here." /> : null}
                {groups.behind.length > 0 ? heading("Behind", true, true) : null}
                {groups.behind.map((item) => row(item, true))}
                {groups.today.length > 0 ? heading("Today", false, groups.behind.length === 0) : null}
                {groups.today.map((item) => row(item, false))}
                {groups.earlier.length > 0 ? heading("Earlier", false, groups.behind.length + groups.today.length === 0) : null}
                {groups.earlier.map((item) => row(item, false))}
            </ScrollView>
            {form?.workflowRunId ? (
                <FormSheet
                    podId={form.podId}
                    runId={form.workflowRunId}
                    title={mode === "sample" ? SAMPLE_SHORT[form.key] ?? form.title : form.title}
                    workflow={form.detail}
                    open
                    onClose={() => setForm(null)}
                    onSent={() => { setSent((was) => new Set(was).add(form.key)); setForm(null); }}
                />
            ) : null}
        </SafeAreaView>
    );
}
