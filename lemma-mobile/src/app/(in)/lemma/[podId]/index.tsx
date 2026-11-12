import { useEffect, useMemo, useState } from "react";
import { KeyboardAvoidingView, ScrollView, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import Animated, { FadeInDown, ReduceMotion } from "react-native-reanimated";
import { SafeAreaView, useSafeAreaInsets } from "react-native-safe-area-context";
import { useApps, useLemma, useLemmas, useNeeds, useOwns, useSections } from "../../../../data/hooks";
import { NOTHING_OWNED } from "../../../../data/model";
import { routes } from "../../../../data/routes";
import type { SideRow } from "../../../../data/types";
import { FixedAskBox } from "../../../../ui/ask-box";
import { BackLink, IconButton, Loading, Meter } from "../../../../ui/chrome";
import { Icon } from "../../../../ui/icons";
import { useIdentity } from "../../../../ui/identity";
import { HeroFace, originOf } from "../../../../ui/lemmas/hero";
import { OneThing } from "../../../../ui/lemmas/one-thing";
import { SECTIONS, isSection, type SectionName } from "../../../../ui/lemmas/sections";
import { Press } from "../../../../ui/press";
import { SideRowLine } from "../../../../ui/rows";
import { Counted, Num, Text } from "../../../../ui/text";
import { color, TOUCH } from "../../../../ui/tokens";
import { FilesIcon } from "../../../../areas/files/glyphs";
import { NotYours } from "../../../../areas/more/begin/not-yours";
import { ShareSheet } from "../../../../areas/more/share/sheet";

/** A Lemma's own page: what it owns and where it stands, in its own colour;
 *  the one thing it needs from you; everything it has, as sections that each
 *  open on a screen of their own; what it did lately; and a box fixed to it.
 *  Opened from its drawer, its face flies on into the header (`from`). */
export default function LemmaPage() {
    const router = useRouter();
    const params = useLocalSearchParams<{ podId: string; tab?: string; from?: string }>();
    const podId = String(params.podId);
    const lemma = useLemma(podId);
    const { pending: listing } = useLemmas();
    const owns = useOwns(lemma);
    const identity = useIdentity(podId);
    const { sections } = useSections();
    const { needs } = useNeeds();
    const { apps } = useApps();
    const [sharing, setSharing] = useState(false);
    const from = useMemo(() => originOf(params.from), [params.from]);
    const insets = useSafeAreaInsets();
    /* An older link to a tab opens that section instead. */
    useEffect(() => {
        if (isSection(params.tab)) router.replace(routes.lemma(podId, params.tab) as never);
    }, [params.tab, podId, router]);
    const now = Date.now();
    const section = sections.find((one) => one.lemma.id === podId) ?? null;
    const mine = needs.filter((item) => item.podId === podId).sort((a, b) => b.sinceMs - a.sinceMs);
    const ownApps = apps.filter((app) => app.podId === podId);
    const recent: SideRow[] = (section?.rows ?? []).slice(0, 5);
    const counts: Partial<Record<SectionName, string>> = useMemo(() => {
        const rows = section?.rows ?? [];
        const chats = rows.filter((row) => row.kind === "chat" || row.kind === "shared" || row.kind === "line").length;
        const running = rows.filter((row) => row.running).length;
        return {
            chats: chats ? String(chats) : undefined,
            apps: ownApps.length ? String(ownApps.length) : undefined,
            runs: running ? `${running} running` : undefined,
        };
    }, [section, ownApps.length]);
    const enter = (index: number) => (from ? FadeInDown.duration(420).delay(140 + index * 50).reduceMotion(ReduceMotion.System) : undefined);

    if (!lemma) {
        return (
            <SafeAreaView style={{ flex: 1, backgroundColor: color.paper }}>
                <View style={{ paddingHorizontal: 6 }}><BackLink label="Lemmas" /></View>
                {/* Not in the list once it has been read: a link to a Lemma
                    that is not one of yours, and the way to ask in (area G). */}
                {listing ? <Loading /> : <View style={{ paddingHorizontal: 20 }}><NotYours key={podId} podId={podId} /></View>}
            </SafeAreaView>
        );
    }

    const said = owns.data;
    return (
        /* Margins from the app's insets, known before the first frame: a page
           fading in shows the frame a SafeAreaView draws before it measures. */
        <View style={{ flex: 1, backgroundColor: identity.wash, paddingTop: insets.top, paddingBottom: insets.bottom }}>
            <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
                <View style={{ flexDirection: "row", alignItems: "center", paddingTop: 4, paddingLeft: 6, paddingRight: 8 }}>
                    <BackLink label="Lemmas" onPress={() => (router.canGoBack() ? router.back() : router.replace(routes.lemmas as never))} />
                    <View style={{ flex: 1 }} />
                    <IconButton name="pencil" size={21} label={`New chat with ${lemma.name}`} onPress={() => router.push(routes.newChat(podId) as never)} />
                    <Press label={"Share " + lemma.name} onPress={() => setSharing(true)} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                        <FilesIcon name="share" size={20} tone={color.ink} />
                    </Press>
                    <IconButton name="more" label="Who has access, where people reach it, share, settings" onPress={() => router.push(routes.lemma(podId, "settings") as never)} />
                </View>
                <ShareSheet podId={podId} subject={{ kind: "lemma" }} open={sharing} onClose={() => setSharing(false)} />

                <ScrollView contentContainerStyle={{ paddingBottom: 16 }} keyboardShouldPersistTaps="handled">
                    <View style={{ paddingTop: 8, paddingHorizontal: 20, gap: 12 }}>
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 14 }}>
                            <HeroFace id={lemma.id} icon={lemma.iconUrl} size={56} from={from} />
                            <Animated.View entering={enter(0)} style={{ flex: 1 }}>
                                <Text accessibilityRole="header" size={24} weight="medium" style={{ letterSpacing: -0.24 }}>{lemma.name}</Text>
                            </Animated.View>
                        </View>
                        <Animated.View entering={enter(1)} style={{ gap: 12 }}>
                            {owns.isPending ? <Text size={18} tone={color.ink3}> </Text> : owns.error ? (
                                <Text size={16} tone={color.ink2}>What it owns couldn’t be read just now.</Text>
                            ) : said?.sentence ? (
                                <Text size={18} style={{ lineHeight: 24.3 }}><Text size={18} tone={color.ink2}>Owns: </Text>{said.sentence}.</Text>
                            ) : (
                                <Text size={16} tone={color.ink2}>{NOTHING_OWNED}</Text>
                            )}
                            {said?.display ? (
                                <View style={{ gap: 8, paddingTop: 2, paddingBottom: 4 }}>
                                    <View style={{ flexDirection: "row", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                                        <Num size={34} weight="medium" tone={color.ink} style={{ letterSpacing: -0.68 }}>{said.display}</Num>
                                        {said.label ? <Text size={14.5} tone={color.ink2}>{said.label}</Text> : null}
                                    </View>
                                    {said.fill !== null ? <Meter fill={said.fill} mark={said.mark} tone={identity.ink} label={`${said.display}${said.note ? ". " + said.note : ""}`} /> : null}
                                    {said.note ? <Counted size={13} tone={said.standing === "behind" ? color.amber : color.ink3}>{said.note}</Counted> : null}
                                </View>
                            ) : said?.note ? <Text size={13} tone={color.ink3}>{said.note}</Text> : null}
                        </Animated.View>
                    </View>

                    <View style={{ paddingTop: 12, paddingHorizontal: 16, gap: 8 }}>
                        {mine[0] ? (
                            <Animated.View entering={enter(2)}>
                                <OneThing item={mine[0]} />
                            </Animated.View>
                        ) : null}
                        {mine.length > 1 ? (
                            <Press role="link" label={`${mine.length - 1} more need you`} onPress={() => router.push(routes.needs as never)} style={{ paddingHorizontal: 4, alignSelf: "flex-start" }}>
                                <Text size={13.5} tone={color.ink3}>{mine.length - 1} more need you</Text>
                            </Press>
                        ) : null}
                    </View>

                    <Animated.View entering={enter(3)} style={{ marginTop: 14, marginHorizontal: 16, borderRadius: 16, backgroundColor: color.paper, paddingHorizontal: 14 }}>
                        {SECTIONS.map((one, index) => (
                            <Press key={one.name} role="link" label={`${one.label}${counts[one.name] ? `, ${counts[one.name]}` : ""}`} onPress={() => router.push(routes.lemma(podId, one.name) as never)} style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 50, borderBottomWidth: index === SECTIONS.length - 1 ? 0 : 1, borderBottomColor: color.line }}>
                                <Text size={16} style={{ flex: 1 }}>{one.label}</Text>
                                {counts[one.name] ? <Counted size={14} tone={one.name === "runs" ? color.violetInk : color.ink3}>{counts[one.name]!}</Counted> : null}
                                <Icon name="forward" size={16} tone={color.ink3} />
                            </Press>
                        ))}
                    </Animated.View>

                    <Animated.View entering={enter(4)} style={{ paddingTop: 16, paddingHorizontal: 16 }}>
                        <Text size={12.5} tone={color.ink3} style={{ marginBottom: 2, marginHorizontal: 4 }}>Recently</Text>
                        {recent.length === 0 ? <Text size={15} tone={color.ink2} style={{ paddingHorizontal: 4, paddingVertical: 8 }}>Nothing yet this week.</Text> : null}
                        {recent.map((row) => <SideRowLine key={row.key} row={row} now={now} glyphWidth={20} pad={4} showRuns />)}
                    </Animated.View>
                </ScrollView>

                <View style={{ paddingHorizontal: 12, paddingTop: 8, paddingBottom: 10, backgroundColor: color.paper, borderTopWidth: 1, borderTopColor: color.line }}>
                    <FixedAskBox name={lemma.name} podId={podId} onSend={(text) => router.push(routes.chat(podId, "new", text) as never)} />
                </View>
            </KeyboardAvoidingView>
        </View>
    );
}
