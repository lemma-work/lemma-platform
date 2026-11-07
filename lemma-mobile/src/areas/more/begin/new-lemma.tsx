import { useMemo, useState, type ReactNode } from "react";
import { ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { useLemmas } from "../../../data/hooks";
import { Loading } from "../../../ui/chrome";
import { Icon } from "../../../ui/icons";
import { Button, Press } from "../../../ui/press";
import { Sheet } from "../../../ui/sheet";
import { Counted, Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { Field, Group, Row, Say, SettingsScreen } from "../../admin/ui";
import { moreRoutes } from "../routes";
import { useStart, type Started } from "./hooks";
import { Making } from "./making";
import { Met, OwnedEnding } from "./met";
import {
    STARTERS, blankStart, freeName, kindOfStarter, nameTaken, ownName, ownStart, ownedLanding, ownsLine, takenSentence,
    type Start, type Starter,
} from "./model";

/** "New Lemma": what should it own? (the `/o` frame's new-Lemma list, with
 *  the web's blank start from `stage/hiring.tsx`).
 *
 *  A starter, or an outcome said in your own words, starts a Lemma named for
 *  it, writes the outcome as its plan, and asks it to set itself up in a new
 *  chat, which then opens so the building can be watched. Only a name (and,
 *  if it is known, the job) starts one with nothing else, and ends on its
 *  face with ways on. Every step is shown as it really happens; a retry
 *  after a failure finishes the Lemma already made. */
export function NewLemmaScreen({ top }: {
    /** Above the starters: where the install part's "You were installing …"
     *  line goes when there is one. */
    top?: ReactNode;
}) {
    const router = useRouter();
    const { lemmas, org, pending } = useLemmas();
    const starting = useStart(org?.id ?? null);
    const [open, setOpen] = useState<Starter | null>(null);
    const [own, setOwn] = useState("");
    const [name, setName] = useState("");
    const [job, setJob] = useState("");
    const [ending, setEnding] = useState<{ start: Start; started: Started } | null>(null);
    const taken = useMemo(() => lemmas.map((one) => one.name), [lemmas]);
    const takenSet = useMemo(() => new Set(taken.map((one) => one.toLowerCase())), [taken]);
    const clash = nameTaken(name, taken);

    const begin = async (start: Start) => {
        setOpen(null);
        const started = await starting.run(start, taken);
        if (!started) return;
        /* An owned start opens the chat where it sets itself up, unless
           there is something to say first: no model to answer with, or a
           sample Lemma standing in for it. */
        if (start.kind === "own" && !started.needsModel && !started.made.standsIn) {
            router.replace(ownedLanding(started.made, started.conversationId, started.asked ?? "") as never);
            return;
        }
        setEnding({ start, started });
    };

    let body: ReactNode;
    if (pending) {
        body = <Loading label="Reading your Lemmas" />;
    } else if (!org) {
        body = (
            <View style={{ gap: 12 }}>
                <Say>You’re not in an organization yet, and a Lemma starts in one.</Say>
                <View style={{ flexDirection: "row" }}>
                    <Button title="Find or start one" onPress={() => router.push(moreRoutes.welcome as never)} />
                </View>
            </View>
        );
    } else if (ending) {
        body = ending.start.kind === "blank"
            ? <Met start={ending.start} started={ending.started} orgName={org.name} />
            : <OwnedEnding start={ending.start} started={ending.started} />;
    } else if (starting.start) {
        const start = starting.start;
        body = (
            <View style={{ gap: 18 }}>
                <Making start={start} done={starting.done} failed={Boolean(starting.error)} />
                {starting.error ? (
                    <View style={{ gap: 12 }}>
                        <Say tone="bad">{starting.error}</Say>
                        <View style={{ flexDirection: "row", gap: 10 }}>
                            <Button title="Choose another" kind="outline" grow onPress={starting.reset} />
                            <Button title="Try again" grow onPress={() => void begin(start)} />
                        </View>
                    </View>
                ) : null}
            </View>
        );
    } else {
        body = (
            <>
                {/* The install part's "You were installing …" line, when the
                    lead mounts one here. */}
                {top}

                <Group label="What should it own?" foot="Owning one starts a new Lemma here, writes its plan, and opens the chat where it sets itself up, so you can watch.">
                    {STARTERS.map((starter, index) => (
                        <Press
                            key={starter.name}
                            role="button"
                            label={`${starter.name}. ${ownsLine(starter.outcome)}`}
                            onPress={() => setOpen(starter)}
                            style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 50, borderBottomWidth: index === STARTERS.length - 1 ? 0 : 1, borderBottomColor: color.line }}
                        >
                            <Text size={16} weight="medium">{starter.name}</Text>
                            <Counted size={14.5} tone={color.ink3} numberOfLines={1} style={{ flex: 1 }}>{starter.outcome}</Counted>
                            <Icon name="forward" size={16} tone={color.ink3} />
                        </Press>
                    ))}
                </Group>

                <Group label="Or say your own">
                    <View style={{ gap: 10, paddingTop: 4 }}>
                        <Field value={own} onChange={setOwn} placeholder="Every invoice over $10,000 gets a second approval" onSubmit={() => { if (own.trim()) void begin(ownStart(freeName(ownName(own), takenSet), own, "week")); }} />
                        <View style={{ flexDirection: "row" }}>
                            <Button title="Own it" disabled={!own.trim()} onPress={() => void begin(ownStart(freeName(ownName(own), takenSet), own, "week"))} />
                        </View>
                    </View>
                </Group>

                <Group label="Or start with only a name" foot="It starts with nothing else, and learns your work as you go.">
                    <View style={{ gap: 12, paddingTop: 4 }}>
                        <Field label="Its name" value={name} onChange={setName} placeholder="Ops, Front desk, Night shift" autoCapitalize="words" error={clash ? takenSentence(name) : null} />
                        <Field label="The job, if you know it yet" value={job} onChange={setJob} placeholder="Watches the inbox and answers what it can" />
                        <View style={{ flexDirection: "row" }}>
                            <Button title={`Start ${name.trim() || "it"}`} disabled={!name.trim() || clash} onPress={() => void begin(blankStart(name, job))} />
                        </View>
                    </View>
                </Group>

                <Group label="Or start from a template">
                    <Row label="Templates" hint="Lemmas others built, ready to install" glyph={<Icon name="apps" size={18} tone={color.ink2} />} onPress={() => router.push(moreRoutes.templates as never)} last />
                </Group>
            </>
        );
    }

    return (
        <SettingsScreen title="New Lemma" note={org && !ending && !starting.start ? `At ${org.name}. It owns something, builds what that takes, runs it, and asks a person when it needs one.` : null}>
            {body}
            <StarterSheet starter={open} taken={takenSet} onClose={() => setOpen(null)} onOwn={(starter, as) => void begin(ownStart(as, starter.outcome, kindOfStarter(starter)))} />
        </SettingsScreen>
    );
}

/** One starter, in full: what it owns, how often it holds it, what it
 *  counts, builds and works in, and "Own this". */
function StarterSheet({ starter, taken, onClose, onOwn }: {
    starter: Starter | null;
    taken: ReadonlySet<string>;
    onClose: () => void;
    onOwn: (starter: Starter, name: string) => void;
}) {
    const as = starter ? freeName(starter.name, taken) : "";
    const facts: [string, string][] = starter ? [
        ["Holds it", starter.kind],
        ["Counts", starter.counts],
        ["Builds", starter.builds],
        ["Works in", starter.works],
        ["For", starter.team],
    ] : [];
    return (
        <Sheet open={Boolean(starter)} onClose={onClose} tall>
            {starter ? (
                <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 6, gap: 16 }}>
                    <View style={{ gap: 6 }}>
                        <Text accessibilityRole="header" size={22} weight="medium">{starter.name}</Text>
                        <Counted size={15.5} tone={color.ink2} style={{ lineHeight: 22 }}>{ownsLine(starter.outcome)}</Counted>
                    </View>
                    <View>
                        {facts.map(([label, value], index) => (
                            <View key={label} style={{ flexDirection: "row", gap: 12, paddingVertical: 11, borderBottomWidth: index === facts.length - 1 ? 0 : 1, borderBottomColor: color.line }}>
                                <Text size={13.5} tone={color.ink3} style={{ width: 70, paddingTop: 1 }}>{label}</Text>
                                <Counted size={15} style={{ flex: 1, lineHeight: 21 }}>{value}</Counted>
                            </View>
                        ))}
                    </View>
                    {as !== starter.name ? <Say>{`There’s a Lemma called ${starter.name} here already, so this one starts as ${as}.`}</Say> : null}
                    <Button title="Own this" height={48} onPress={() => onOwn(starter, as)} />
                </ScrollView>
            ) : null}
        </Sheet>
    );
}
