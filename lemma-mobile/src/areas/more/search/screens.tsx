import { useMemo, useState, type ReactNode } from "react";
import { ActivityIndicator, KeyboardAvoidingView, ScrollView, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLemmas } from "../../../data/hooks";
import { NEW_CHAT, routes } from "../../../data/routes";
import type { Lemma } from "../../../data/types";
import { Glyph } from "../../../thread/glyphs";
import { LemmaPicker } from "../../../ui/ask-box";
import { BackLink } from "../../../ui/chrome";
import { LemmaFace, PersonFace } from "../../../ui/face";
import { Icon, type IconName } from "../../../ui/icons";
import { Press } from "../../../ui/press";
import { Num, Text } from "../../../ui/text";
import { color, font, TOUCH } from "../../../ui/tokens";
import { problemOf, ReadProblem, Say } from "../../admin/ui";
import { FilesIcon } from "../../files/glyphs";
import { glyphOf, nameOf } from "../../files/model";
import { useDiscuss, useSearch } from "./hooks";
import {
    askTarget,
    destinationOf,
    failedLine,
    foundOf,
    functionChatFallback,
    headingOf,
    hitLabel,
    NOTHING_MATCHES,
    placeholderOf,
    quietLine,
    secondLine,
    skippedLine,
    whenOf,
    type Found,
} from "./model";
import { highlight, type Hit, type SearchKind } from "@web/search/matching";

/** One box for everything in every Lemma: the web's search palette
 *  (`search/palette.tsx`) and the `/o` frame's ask (`outcomes/palette.tsx`),
 *  as a screen. Opened from Home and the Lemmas tab, where no Lemma is
 *  current, it searches all of them; a chip narrows it to one, and
 *  `?pod=` opens it on one.
 *
 *  What is typed is first offered as a question to a Lemma, then matched:
 *  what each Lemma holds at once, what the server finds a moment later, a
 *  heading per kind, the matched letters in medium, the Lemma named under
 *  each hit when every Lemma is searched. What could not be reached is
 *  said, never left out. */
export function SearchScreen({ podId }: { podId: string | null }) {
    const router = useRouter();
    const all = useLemmas();
    const [chosen, setChosen] = useState<string | null>(podId);
    const [text, setText] = useState("");
    const [asking, setAsking] = useState<string | null>(null);
    const [picking, setPicking] = useState(false);
    /* A Lemma named in the address that is not one of these is no scope. */
    const scoped = chosen ? all.lemmas.find((one) => one.id === chosen) ?? null : null;
    const scope = scoped?.id ?? null;
    const outcome = useSearch(scope, text);
    const discuss = useDiscuss();
    const typed = text.trim();
    const names = useMemo(() => new Map(all.lemmas.map((one) => [one.id, one])), [all.lemmas]);
    const target = askTarget(scope, asking, all.lemmas);
    const failed = failedLine(outcome.failures, (id) => names.get(id)?.name ?? "a Lemma", scoped !== null);
    const skipped = typed ? skippedLine(outcome.skippedTables) : null;
    const now = Date.now();

    const open = (found: Found) => {
        const where = destinationOf(found);
        if ("href" in where) {
            router.push(where.href as never);
            return;
        }
        const { podId: pod, name, label } = where.discuss;
        discuss.mutate({ podId: pod, name, label }, {
            onSuccess: (id) => router.push((id ? routes.chat(pod, id) : functionChatFallback(pod, label)) as never),
        });
    };

    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <View style={{ flexDirection: "row", alignItems: "center", paddingTop: 4, paddingLeft: 6, paddingRight: 8 }}>
                <BackLink />
            </View>
            <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
                <View style={{ paddingHorizontal: 16, paddingBottom: 10 }}>
                    <SearchField value={text} onChange={setText} placeholder={placeholderOf(scoped?.name ?? null)} busy={outcome.searching} />
                </View>
                {all.lemmas.length > 1 ? <ScopeChips lemmas={all.lemmas} scope={scope} onPick={setChosen} /> : null}
                <ScrollView keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag" contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 6, paddingBottom: 28 }}>
                    {typed && target ? (
                        <AskRow lemma={target} words={typed} canPick={scoped === null && all.lemmas.length > 1} onAsk={() => router.push(routes.chat(target.id, NEW_CHAT, typed) as never)} onPick={() => setPicking(true)} />
                    ) : null}
                    {failed ? <ReadProblem said={failed} onRetry={outcome.retry} /> : null}
                    {discuss.isError ? <Say tone="bad">{problemOf(discuss.error, "Couldn’t open the chat about it. Try again.")}</Say> : null}

                    {typed ? (
                        <>
                            {outcome.groups.map((group) => {
                                const heading = headingOf(group);
                                return (
                                    <View key={group.kind + ":" + group.label} style={{ paddingTop: 14 }}>
                                        <Text size={12.5} tone={color.ink3} accessibilityRole="header" style={{ paddingBottom: 2 }}>{heading}</Text>
                                        {group.hits.map((hit, index) => {
                                            const found = foundOf(hit);
                                            const lemma = found ? names.get(found.podId) ?? null : null;
                                            const second = secondLine(hit, group, scoped === null ? lemma?.name ?? null : null);
                                            const busy = discuss.isPending && found?.open.kind === "function" && discuss.variables?.podId === found.podId && discuss.variables?.name === found.open.name;
                                            return (
                                                <HitRow
                                                    key={hit.kind + ":" + hit.id}
                                                    hit={hit}
                                                    second={second}
                                                    glyph={<HitGlyph hit={hit} lemma={lemma} />}
                                                    end={busy ? <ActivityIndicator size="small" color={color.ink3} /> : whenOf(found, now)}
                                                    last={index === group.hits.length - 1}
                                                    onPress={found && !discuss.isPending ? () => open(found) : undefined}
                                                />
                                            );
                                        })}
                                    </View>
                                );
                            })}
                            {outcome.hits.length === 0 && !outcome.searching && !outcome.reading ? (
                                <Text size={15} tone={color.ink2} style={{ paddingTop: 18 }}>{NOTHING_MATCHES}</Text>
                            ) : null}
                            {skipped ? <Text size={13} tone={color.ink3} style={{ paddingTop: 16 }}>{skipped}</Text> : null}
                        </>
                    ) : (
                        <Jump lemmas={all.lemmas} pending={all.pending} error={all.error} onRetry={() => void all.refetch()} scopeName={scoped?.name ?? null} onOpen={(lemma) => router.push(routes.lemma(lemma.id) as never)} />
                    )}
                </ScrollView>
            </KeyboardAvoidingView>
            <LemmaPicker lemmas={all.lemmas} chosen={target?.id ?? null} open={picking} onClose={() => setPicking(false)} onPick={setAsking} />
        </SafeAreaView>
    );
}

/** The box, with the server's half spinning at its end while it is asked. */
function SearchField({ value, onChange, placeholder, busy }: { value: string; onChange: (next: string) => void; placeholder: string; busy: boolean }) {
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8, height: TOUCH + 2, borderRadius: 12, backgroundColor: color.wash, paddingHorizontal: 12 }}>
            <Icon name="search" size={18} tone={color.ink3} />
            <TextInput
                accessibilityLabel={placeholder}
                value={value}
                onChangeText={onChange}
                placeholder={placeholder}
                placeholderTextColor={color.ink3}
                autoFocus
                autoCorrect={false}
                autoCapitalize="none"
                returnKeyType="search"
                style={{ flex: 1, height: TOUCH, fontFamily: font.regular, fontSize: 16, color: color.ink, padding: 0 }}
            />
            {busy ? <ActivityIndicator accessibilityLabel="Searching" size="small" color={color.ink3} /> : null}
            {value ? (
                <Press label="Clear the search" onPress={() => onChange("")} hitSlop={10} style={{ width: 28, height: 28, alignItems: "center", justifyContent: "center" }}>
                    <Icon name="close" size={16} tone={color.ink3} />
                </Press>
            ) : null}
        </View>
    );
}

/** Every Lemma, or one: a pill each, the chosen one filled with ink. */
function ScopeChips({ lemmas, scope, onPick }: { lemmas: readonly Lemma[]; scope: string | null; onPick: (id: string | null) => void }) {
    const choices: { id: string | null; label: string }[] = [{ id: null, label: "All Lemmas" }, ...lemmas.map((one) => ({ id: one.id, label: one.name }))];
    return (
        <View style={{ height: 44 }}>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} keyboardShouldPersistTaps="handled" accessibilityRole="radiogroup" contentContainerStyle={{ paddingHorizontal: 16, gap: 8, alignItems: "center" }}>
                {choices.map((choice) => {
                    const on = choice.id === scope;
                    return (
                        <Press
                            key={choice.id ?? "all"}
                            role="radio"
                            label={choice.id ? `Search ${choice.label}` : "Search every Lemma"}
                            accessibilityState={{ selected: on }}
                            onPress={() => onPick(choice.id)}
                            hitSlop={{ top: 4, bottom: 4 }}
                            style={{ height: 36, paddingHorizontal: 14, borderRadius: 999, justifyContent: "center", backgroundColor: on ? color.ink : color.paper, borderWidth: on ? 0 : 1, borderColor: color.line2 }}
                        >
                            <Text size={14} tone={on ? color.onInk : color.ink} numberOfLines={1}>{choice.label}</Text>
                        </Press>
                    );
                })}
            </ScrollView>
        </View>
    );
}

/** The words as a question: "Ask Support", opening a chat with them sent,
 *  as Home's box does. Searching every Lemma, who hears it can be changed. */
function AskRow({ lemma, words, canPick, onAsk, onPick }: { lemma: Lemma; words: string; canPick: boolean; onAsk: () => void; onPick: () => void }) {
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8, borderBottomWidth: 1, borderBottomColor: color.line }}>
            <Press role="button" label={`Ask ${lemma.name}: ${words}`} onPress={onAsk} style={{ flex: 1, minWidth: 0, flexDirection: "row", alignItems: "center", gap: 10, minHeight: 58, paddingVertical: 6 }}>
                <View style={{ width: 28, alignItems: "center" }}>
                    <LemmaFace id={lemma.id} icon={lemma.iconUrl} size={26} />
                </View>
                <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
                    <Text size={16} numberOfLines={1}>Ask {lemma.name}</Text>
                    <Text size={13} tone={color.ink3} numberOfLines={1}>“{words}”</Text>
                </View>
            </Press>
            {canPick ? (
                <Press label="Ask another Lemma" onPress={onPick} hitSlop={6} style={{ flexDirection: "row", alignItems: "center", gap: 4, height: 32, paddingHorizontal: 10, borderRadius: 999, backgroundColor: color.wash }}>
                    <Text size={13.5}>Change</Text>
                    <Icon name="down" size={12} width={2} tone={color.ink2} />
                </Press>
            ) : (
                <Icon name="forward" size={16} tone={color.ink3} />
            )}
        </View>
    );
}

/** One hit: what it is, its name with the typed letters in medium, a second
 *  line only when it says something, and when it last moved for a chat. */
function HitRow({ hit, second, glyph, end, last, onPress }: { hit: Hit; second: string | null; glyph: ReactNode; end: ReactNode; last: boolean; onPress?: () => void }) {
    return (
        <Press
            role="link"
            label={hitLabel(hit, second)}
            disabled={!onPress}
            onPress={onPress}
            style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: second ? 58 : 50, paddingVertical: 6, borderBottomWidth: last ? 0 : 1, borderBottomColor: color.line }}
        >
            <View style={{ width: 28, alignItems: "center" }}>{glyph}</View>
            <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
                <Text size={16} numberOfLines={1}>
                    {highlight(hit.title, hit.ranges).map((piece, index) => (piece.hit ? <Text key={index} size={16} weight="medium">{piece.text}</Text> : piece.text))}
                </Text>
                {second ? <Text size={13} tone={color.ink3} numberOfLines={1}>{second}</Text> : null}
            </View>
            {typeof end === "string" ? <Num size={13} tone={color.ink3}>{end}</Num> : end}
        </Press>
    );
}

const KIND_ICON: Partial<Record<SearchKind, IconName>> = {
    conversation: "chat",
    record: "table",
    app: "app",
    workflow: "automation",
    table: "table",
    schedule: "clock",
};

function HitGlyph({ hit, lemma }: { hit: Hit; lemma: Lemma | null }) {
    if (hit.kind === "teammate") return <LemmaFace id={foundOf(hit)?.podId ?? hit.id} icon={lemma?.iconUrl ?? null} size={24} />;
    if (hit.kind === "person") return <PersonFace name={hit.title} size={24} />;
    if (hit.kind === "agent") return <Glyph name="agent" size={17} tone={color.ink2} />;
    if (hit.kind === "function") return <Glyph name="code" size={17} tone={color.ink2} />;
    if (hit.kind === "doc") {
        const open = foundOf(hit)?.open;
        const path = open?.kind === "file" ? open.path : hit.title;
        return <FilesIcon name={glyphOf({ kind: "file", name: nameOf(path), mime: undefined })} size={18} tone={color.ink2} />;
    }
    return <Icon name={KIND_ICON[hit.kind] ?? "file"} size={17} tone={color.ink2} />;
}

/** An empty box: what it finds, then the Lemmas, to go straight to one. */
function Jump({ lemmas, pending, error, onRetry, scopeName, onOpen }: {
    lemmas: readonly Lemma[];
    pending: boolean;
    error: unknown;
    onRetry: () => void;
    scopeName: string | null;
    onOpen: (lemma: Lemma) => void;
}) {
    return (
        <View style={{ gap: 14 }}>
            <Text size={13.5} tone={color.ink3} style={{ lineHeight: 19 }}>{quietLine(scopeName)}</Text>
            {pending ? (
                <View accessibilityLabel="Loading" style={{ paddingVertical: 14, alignItems: "flex-start" }}><ActivityIndicator color={color.ink3} /></View>
            ) : null}
            {error ? <ReadProblem said={problemOf(error, "Couldn’t load your Lemmas.")} onRetry={onRetry} /> : null}
            {lemmas.length > 0 ? (
                <View>
                    <Text size={12.5} tone={color.ink3} accessibilityRole="header" style={{ paddingBottom: 2 }}>Lemmas</Text>
                    {lemmas.map((lemma, index) => (
                        <Press
                            key={lemma.id}
                            role="link"
                            label={lemma.description ? `${lemma.name}, ${lemma.description}` : lemma.name}
                            onPress={() => onOpen(lemma)}
                            style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: lemma.description ? 58 : 50, paddingVertical: 6, borderBottomWidth: index === lemmas.length - 1 ? 0 : 1, borderBottomColor: color.line }}
                        >
                            <View style={{ width: 28, alignItems: "center" }}>
                                <LemmaFace id={lemma.id} icon={lemma.iconUrl} size={26} />
                            </View>
                            <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
                                <Text size={16} numberOfLines={1}>{lemma.name}</Text>
                                {lemma.description ? <Text size={13} tone={color.ink3} numberOfLines={1}>{lemma.description}</Text> : null}
                            </View>
                            <Icon name="forward" size={16} tone={color.ink3} />
                        </Press>
                    ))}
                </View>
            ) : !pending && !error ? (
                <Text size={15} tone={color.ink2}>No Lemmas here yet.</Text>
            ) : null}
        </View>
    );
}
