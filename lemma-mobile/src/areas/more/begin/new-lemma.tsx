import { useMemo, useState, type ReactNode } from "react";
import { Platform, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import DateTimePicker, { DateTimePickerAndroid, type DateTimePickerEvent } from "@react-native-community/datetimepicker";
import { useLemmas } from "../../../data/hooks";
import { routes } from "../../../data/routes";
import { isMissing } from "../../admin/words";
import { useSession } from "../../../session/session";
import { PRESETS } from "../../../session/server";
import { Loading } from "../../../ui/chrome";
import { LemmaFace } from "../../../ui/face";
import { Icon } from "../../../ui/icons";
import { Button, Press } from "../../../ui/press";
import { Sheet } from "../../../ui/sheet";
import { Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { dayToDate, isoDay, spokenDay } from "../../admin/account/zones";
import { Field, Group, ReadProblem, Say, SettingsScreen } from "../../admin/ui";
import { moreRoutes } from "../routes";
import { useShelf, useStartLemma, type Started } from "./hooks";
import { castIcon, kindSaid, nextFor, sharedOpen, standInWords, startedLanding, workLine } from "./model";
import type { StartRequest } from "./source";
import { ownName, sentenceFor, shelfOf, type ShelfCard, type ShelfWork } from "@web/outcomes/model";

/** "New Lemma" (the frame's shelf, `outcomes/shelf.tsx`): the kind of work,
 *  then what a Lemma can own there, from the templates the server ships; or
 *  what it owns in your own words; or something someone shared. Starting
 *  one (`POST /pods/start`) wears the template's face and opens the chat it
 *  sets itself up in, where a strip says what its template is bringing in. */
export function NewLemmaScreen({ top }: {
    /** Above the shelf: where the install part's "You were installing …"
     *  line goes when there is one. */
    top?: ReactNode;
}) {
    const router = useRouter();
    const { org, pending } = useLemmas();
    const shelf = useShelf();
    const works = useMemo(() => shelfOf(shelf.data ?? []), [shelf.data]);
    const starting = useStartLemma(org?.id ?? null);
    const [work, setWork] = useState<ShelfWork | null>(null);
    const [own, setOwn] = useState(false);
    const [ending, setEnding] = useState<Started | null>(null);

    const begin = (request: StartRequest, face: string | null) => {
        starting.mutate({ request, face }, {
            onSuccess: (started) => {
                setWork(null);
                setOwn(false);
                /* Straight into its setup chat, unless there is something to
                   say first: no model to answer with, a face that would not
                   save, or a sample Lemma standing in for it. */
                if (!started.needsModel && started.faceSaved && !started.standsIn) {
                    router.replace(startedLanding(started) as never);
                    return;
                }
                setEnding(started);
            },
        });
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
        body = <Ending started={ending} />;
    } else {
        body = (
            <>
                {/* The install part's "You were installing …" line, when the
                    lead mounts one here. */}
                {top}

                <Group label="What do you do?" foot="Pick your work. Each comes with what a Lemma can own there, and works with the tools you already use.">
                    {shelf.isPending ? <Loading label="Reading what a Lemma can start from" /> : shelf.isError && isMissing(shelf.error) ? (
                        /* A server from before the shelf (#1194) has no templates and no one-call start. */
                        <View style={{ gap: 12, paddingVertical: 8 }}>
                            <Say>This server is older than this app, so a Lemma can’t be started from here yet. Start one on the web.</Say>
                            <View style={{ flexDirection: "row" }}>
                                <Button title="Open the web" kind="outline" onPress={() => router.push(routes.web("", "/", "New Lemma") as never)} />
                            </View>
                        </View>
                    ) : shelf.isError ? (
                        <ReadProblem said="Couldn’t read what a Lemma can start from." onRetry={() => void shelf.refetch()} />
                    ) : (
                        <>
                            {works.map((one) => (
                                <ShelfRow key={one.id} face={one.face} title={one.label} line={workLine(one)} place={one.places.length ? `Usually in ${one.places.join(", ")}` : null} count={`${one.cards.length} to own`} onPress={() => { starting.reset(); setWork(one); }} />
                            ))}
                            <ShelfRow face={null} title="Something else" line="Say what it should own, in your words." place={null} count={null} onPress={() => { starting.reset(); setOwn(true); }} last />
                        </>
                    )}
                </Group>

                <SharedLink />
            </>
        );
    }

    return (
        <SettingsScreen title="New Lemma" note={org && !ending ? `At ${org.name}. It owns something, builds what that takes, runs it, and asks a person when it needs one.` : null}>
            {body}
            <WorkSheet work={work} orgId={org?.id ?? ""} orgName={org?.name ?? ""} busy={starting.isPending} error={starting.error ? starting.error.message : null} onStart={begin} onClose={() => setWork(null)} />
            <OwnWordsSheet open={own} orgId={org?.id ?? ""} busy={starting.isPending} error={starting.error ? starting.error.message : null} onStart={begin} onClose={() => setOwn(false)} />
        </SettingsScreen>
    );
}

/** One kind of work on the shelf: its face, what it owns there, and where. */
function ShelfRow({ face, title, line, place, count, onPress, last = false }: {
    face: string | null;
    title: string;
    line: string;
    place: string | null;
    count: string | null;
    onPress: () => void;
    last?: boolean;
}) {
    const icon = castIcon(face);
    return (
        <Press
            role="button"
            label={[title, line, place].filter(Boolean).join(". ")}
            onPress={onPress}
            style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: 64, paddingVertical: 10, borderBottomWidth: last ? 0 : 1, borderBottomColor: color.line }}
        >
            {icon ? <LemmaFace id={title} icon={icon} size={36} /> : (
                <View style={{ width: 36, height: 36, borderRadius: 10, backgroundColor: color.wash, alignItems: "center", justifyContent: "center" }}>
                    <Icon name="plus" size={16} tone={color.ink2} />
                </View>
            )}
            <View style={{ flex: 1, gap: 2 }}>
                <Text size={16} weight="medium" numberOfLines={1}>{title}</Text>
                <Text size={14} tone={color.ink2} numberOfLines={2} style={{ lineHeight: 19 }}>{line}</Text>
                {place ? <Text size={13} tone={color.ink3} numberOfLines={1}>{place}</Text> : null}
            </View>
            {count ? <Text size={13} tone={color.ink3}>{count}</Text> : null}
            <Icon name="forward" size={16} tone={color.ink3} />
        </Press>
    );
}

/** The Lemmas one kind of work can start, and what each brings and never
 *  does; a finish line asks for its date. */
function WorkSheet({ work, orgId, orgName, busy, error, onStart, onClose }: {
    work: ShelfWork | null;
    orgId: string;
    orgName: string;
    busy: boolean;
    error: string | null;
    onStart: (request: StartRequest, face: string | null) => void;
    onClose: () => void;
}) {
    const [chosenId, setChosenId] = useState<string | null>(null);
    const [due, setDue] = useState("");
    const chosen: ShelfCard | null = work ? work.cards.find((card) => card.id === chosenId) ?? work.cards[0] : null;
    const needsDate = chosen?.kind === "date";
    const close = () => {
        setChosenId(null);
        setDue("");
        onClose();
    };
    return (
        <Sheet open={Boolean(work)} onClose={close} tall>
            {work && chosen ? (
                <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 6, gap: 18 }} keyboardShouldPersistTaps="handled">
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                        <LemmaFace id={chosen.name} icon={castIcon(chosen.face)} size={48} />
                        <Text accessibilityRole="header" size={22} weight="medium">{work.label}</Text>
                    </View>

                    <Part label="What it can own">
                        {work.cards.map((card, index) => {
                            const picked = card.id === chosen.id;
                            return (
                                <Press
                                    key={card.id}
                                    role="radio"
                                    label={sentenceFor(card, picked ? due : null)}
                                    onPress={() => setChosenId(card.id)}
                                    style={{ flexDirection: "row", alignItems: "flex-start", gap: 12, paddingVertical: 11, borderBottomWidth: index === work.cards.length - 1 ? 0 : 1, borderBottomColor: color.line }}
                                >
                                    <View style={{ width: 20, height: 20, borderRadius: 10, borderWidth: picked ? 6 : 1.5, borderColor: picked ? color.violet : color.ink3, marginTop: 1 }} />
                                    <View style={{ flex: 1, gap: 2 }}>
                                        <Text size={15.5} style={{ lineHeight: 21 }}>{sentenceFor(card, picked ? due || null : null)}</Text>
                                        <Text size={13} tone={color.ink3}>{card.name} · {kindSaid(card.kind)}</Text>
                                    </View>
                                </Press>
                            );
                        })}
                    </Part>

                    {chosen.place ? (
                        <Part label="Where it works" note="It asks which one once it starts">
                            <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>Usually {chosen.place}, or wherever this work already happens for you. It reads what is there, and keeps reading.</Text>
                        </Part>
                    ) : null}
                    {chosen.brings.length ? <Part label="What it brings"><Lines lines={chosen.brings} /></Part> : null}
                    {chosen.never.length ? <Part label="Never"><Lines lines={chosen.never} /></Part> : null}

                    <View style={{ gap: 10 }}>
                        {needsDate ? <DueField value={due} onChange={setDue} /> : null}
                        {error ? <Say tone="bad">{error}</Say> : null}
                        <Button
                            title={busy ? "Starting…" : `Start ${chosen.name}`}
                            height={48}
                            busy={busy}
                            disabled={needsDate && !due}
                            onPress={() => onStart({ orgId, template: chosen.id, due: needsDate ? due : null }, chosen.face)}
                        />
                        <Say>{`Makes a Lemma called ${chosen.name} in ${orgName}. It sets itself up in a chat with you.`}</Say>
                    </View>
                </ScrollView>
            ) : null}
        </Sheet>
    );
}

/** A Lemma from its owner's own words: what it owns, and a name. It starts
 *  without a number and sets how it is counted once it has built its tables. */
function OwnWordsSheet({ open, orgId, busy, error, onStart, onClose }: {
    open: boolean;
    orgId: string;
    busy: boolean;
    error: string | null;
    onStart: (request: StartRequest, face: string | null) => void;
    onClose: () => void;
}) {
    const [sentence, setSentence] = useState("");
    const [kind, setKind] = useState<"week" | "date">("week");
    const [due, setDue] = useState("");
    const [name, setName] = useState<string | null>(null);
    const called = name ?? (sentence.trim() ? ownName(sentence) : "");
    const ready = Boolean(sentence.trim() && called.trim() && (kind === "week" || due));
    return (
        <Sheet open={open} onClose={onClose} tall>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 6, gap: 16 }} keyboardShouldPersistTaps="handled">
                <View style={{ gap: 6 }}>
                    <Text accessibilityRole="header" size={22} weight="medium">What should it own?</Text>
                    <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>One sentence a person can check. It builds the tables that count it, then sets how it is counted.</Text>
                </View>
                <Field label="It owns" value={sentence} onChange={setSentence} placeholder="Every invoice is paid within 30 days" autoFocus />
                <View style={{ flexDirection: "row", gap: 8 }} accessibilityRole="radiogroup">
                    <Chip label="A bar every week" on={kind === "week"} onPress={() => setKind("week")} />
                    <Chip label="A finish line by a date" on={kind === "date"} onPress={() => setKind("date")} />
                </View>
                {kind === "date" ? <DueField value={due} onChange={setDue} /> : null}
                <Field label="Called" value={called} onChange={setName} placeholder="Invoices" autoCapitalize="words" />
                {error ? <Say tone="bad">{error}</Say> : null}
                <Button
                    title={busy ? "Starting…" : "Start it"}
                    height={48}
                    busy={busy}
                    disabled={!ready}
                    onPress={() => onStart({ orgId, name: called.trim(), sentence: sentence.trim(), kind, due: kind === "date" ? due : null }, null)}
                />
            </ScrollView>
        </Sheet>
    );
}

/** A link to a Lemma or a plugin someone shared, opened where this app
 *  installs it: what it brings and needs is shown before anything is added. */
function SharedLink() {
    const router = useRouter();
    const { server } = useSession();
    const [link, setLink] = useState("");
    const [wrong, setWrong] = useState(false);
    const look = () => {
        const origins = [server.web, ...PRESETS.map((one) => one.web)].map((web) => web.replace(/\/+$/, ""));
        const path = sharedOpen(link, origins);
        setWrong(!path);
        if (path) router.push(path as never);
    };
    return (
        <Group label="Bring one someone shared">
            <View style={{ gap: 10, paddingTop: 4 }}>
                <Field value={link} onChange={(next) => { setLink(next); setWrong(false); }} placeholder="A GitHub repository, or a link someone shared from Lemma" keyboardType="url" autoCapitalize="none" onSubmit={look} error={wrong ? "That isn’t a link this can open. Paste a GitHub repository or a Lemma share link." : null} />
                {!wrong ? <Say>You see what it brings and what it needs before anything is added.</Say> : null}
                <View style={{ flexDirection: "row" }}>
                    <Button title="Look" kind="outline" disabled={!link.trim()} onPress={look} />
                </View>
            </View>
        </Group>
    );
}

/** Said when a start cannot simply open its chat: no model to answer with,
 *  a face that would not save, or sample data standing a Lemma in. */
function Ending({ started }: { started: Started }) {
    const router = useRouter();
    const standIn = started.standsIn ?? null;
    return (
        <View style={{ gap: 14 }}>
            <LemmaFace id={standIn?.id ?? started.id} icon={null} size={88} />
            <View style={{ gap: 6 }}>
                <Text accessibilityRole="header" size={24} weight="medium" style={{ letterSpacing: -0.24, lineHeight: 29 }}>
                    {started.needsModel ? `${started.name} needs an AI model before it can reply.` : `${started.name} is setting itself up`}
                </Text>
                <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>
                    {started.needsModel ? "It has been asked to set itself up, and can answer once this organization has a model." : "It was asked to build what it needs to own this, in a chat with you."}
                </Text>
            </View>
            {started.needsModel ? (
                <View style={{ flexDirection: "row" }}>
                    <Button title="Set up a model" kind="outline" onPress={() => router.push(nextFor.models() as never)} />
                </View>
            ) : null}
            {!started.faceSaved ? <Say>{`Couldn’t save the face it was given, so ${started.name} kept the one it came with.`}</Say> : null}
            {standIn ? <Say>{standInWords(started.name, standIn.name)}</Say> : null}
            <Button title="Open the chat" height={48} onPress={() => router.replace(startedLanding(started) as never)} />
        </View>
    );
}

function Part({ label, note, children }: { label: string; note?: string; children: ReactNode }) {
    return (
        <View style={{ gap: 8 }}>
            <View style={{ flexDirection: "row", alignItems: "baseline", gap: 8 }}>
                <Text size={13} tone={color.ink3} style={{ letterSpacing: 0.3 }}>{label.toUpperCase()}</Text>
                {note ? <Text size={12.5} tone={color.ink3} style={{ flex: 1 }} numberOfLines={1}>{note}</Text> : null}
            </View>
            {children}
        </View>
    );
}

function Lines({ lines }: { lines: readonly string[] }) {
    return (
        <View style={{ gap: 8 }}>
            {lines.map((line) => (
                <View key={line} style={{ flexDirection: "row", gap: 10 }}>
                    <Text size={15} tone={color.ink3}>–</Text>
                    <Text size={15} style={{ flex: 1, lineHeight: 21 }}>{line}</Text>
                </View>
            ))}
        </View>
    );
}

function Chip({ label, on, onPress }: { label: string; on: boolean; onPress: () => void }) {
    return (
        <Press role="radio" label={label} onPress={onPress} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 999, borderWidth: 1, borderColor: on ? color.violet : color.line, backgroundColor: on ? color.violetWash : "transparent" }}>
            <Text size={14} tone={on ? color.violetInk : color.ink2}>{label}</Text>
        </Press>
    );
}

/** A finish line's date: Android's dialog, iOS's compact picker. Never in the past. */
function DueField({ value, onChange }: { value: string; onChange: (next: string) => void }) {
    const today = new Date();
    const held = dayToDate(value);
    const pick = () => {
        DateTimePickerAndroid.open({
            value: held ?? today,
            mode: "date",
            minimumDate: today,
            onChange: (event: DateTimePickerEvent, date?: Date) => {
                if (event.type === "set" && date) onChange(isoDay(date));
            },
        });
    };
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 44 }}>
            <Text size={15} tone={color.ink2}>By</Text>
            {Platform.OS === "android" ? (
                <Press role="button" label={value ? `By ${spokenDay(value)}` : "Pick a date"} onPress={pick} style={{ flex: 1, paddingVertical: 10 }}>
                    <Text size={15.5} tone={value ? color.ink : color.violetInk}>{value ? spokenDay(value) : "Pick a date"}</Text>
                </Press>
            ) : held ? (
                <DateTimePicker value={held} mode="date" display="compact" minimumDate={today} onChange={(_, date) => date && onChange(isoDay(date))} />
            ) : (
                /* Nothing picked is said as nothing picked, not as today. */
                <Press role="button" label="Pick a date" onPress={() => onChange(isoDay(new Date(today.getTime() + 7 * 86_400_000)))} style={{ flex: 1, paddingVertical: 10 }}>
                    <Text size={15.5} tone={color.violetInk}>Pick a date</Text>
                </Press>
            )}
        </View>
    );
}
