import { useMemo, useState } from "react";
import { KeyboardAvoidingView, Platform, RefreshControl, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { useApps, useLemmas, useMe, useNeeds, useOpenLemma, useOrg, usePulse, useSections } from "../../../../data/hooks";
import { foldSection } from "../../../../data/model";
import { routes } from "../../../../data/routes";
import type { AppItem, LemmaSection } from "../../../../data/types";
import { chatRoutes } from "../../../../areas/chat/routes";
import { holdFiles } from "../../../../thread/hand-over";
import { moreRoutes } from "../../../../areas/more/routes";
import { HomeOverlays } from "../../../../areas/more/home-overlays";
import { ResumeInstall } from "../../../../areas/more/install/resume";
import { Arrival } from "../../../../areas/more/welcome/arrival";
import { TourTarget } from "../../../../areas/more/tour/target";
import { AppMark } from "../../../../ui/app-mark";
import { AskBox } from "../../../../ui/ask-box";
import { Calm } from "../../../../ui/calm";
import { IconButton, Loading, Problem } from "../../../../ui/chrome";
import { LemmaFace } from "../../../../ui/face";
import { Icon, LemmaMark } from "../../../../ui/icons";
import { LiveBars } from "../../../../ui/live-bars";
import { MeButton } from "../../../../ui/me-button";
import { Button, Press } from "../../../../ui/press";
import { SideRowLine } from "../../../../ui/rows";
import { Num, Text } from "../../../../ui/text";
import { color } from "../../../../ui/tokens";
import { Wash } from "../../../../ui/wash";

/** Home: the web sidebar as a page, in its light. What needs you, said
 *  once; the ask box, which is the web's New chat; then every Lemma, busiest
 *  first. The one you were last in is unfolded on a lit card -- its apps,
 *  then its chats -- as the sidebar unfolds the Lemma you are in; the rest
 *  are a line each, with live bars while one works and a warm count of what
 *  waits on you. */
export default function Home() {
    const router = useRouter();
    const me = useMe();
    const { org, orgs, pending: orgsPending } = useOrg();
    const { lemmas, pending: lemmasPending, error } = useLemmas();
    const { needs, pending } = useNeeds();
    const { sections, pending: sectionsPending } = useSections();
    const { refetch, fetching } = usePulse();
    const { apps } = useApps();
    const { openId } = useOpenLemma();
    const [asking, setAsking] = useState<string | null>(null);
    const now = Date.now();
    const first = me.data?.firstName;
    const named = first ? `, ${first}` : "";
    const hour = new Date(now).getHours();
    const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
    const headline = needs.length > 0 ? `${needs.length} ${needs.length === 1 ? "thing needs" : "things need"} you${named}.` : `${greeting}${named}.`;
    const open = sections.find((section) => section.lemma.id === openId) ?? sections[0] ?? null;
    const chosen = asking ?? open?.lemma.id ?? lemmas[0]?.id ?? null;
    const waitingIn = useMemo(() => {
        const counts = new Map<string, number>();
        for (const item of needs) counts.set(item.podId, (counts.get(item.podId) ?? 0) + 1);
        return counts;
    }, [needs]);

    return (
        <SafeAreaView edges={["top"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <Wash />
            <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 6, paddingTop: 6, paddingRight: 14, paddingLeft: 18, paddingBottom: 4 }}>
                    <LemmaMark size={20} />
                    <Press
                        label={`${org?.name ?? "Organization"}${orgs.length > 1 ? ". Change" : ""}`}
                        onPress={() => router.push(routes.account as never)}
                        style={{ flexDirection: "row", alignItems: "center", gap: 4, height: 36, paddingHorizontal: 4 }}
                    >
                        <Text size={17}>{org?.name ?? " "}</Text>
                        {org ? <Icon name="down" size={14} width={2} tone={color.ink2} /> : null}
                    </Press>
                    <View style={{ flex: 1 }} />
                    <TourTarget id="search">
                        <IconButton name="search" label="Ask or find" onPress={() => router.push(moreRoutes.search as never)} />
                    </TourTarget>
                    <MeButton />
                </View>

                <ScrollView
                    contentContainerStyle={{ paddingTop: 8, paddingBottom: 28 }}
                    refreshControl={<RefreshControl refreshing={fetching && !pending} onRefresh={() => void refetch()} tintColor={color.ink3} />}
                    keyboardShouldPersistTaps="handled"
                >
                    {error ? <View style={{ paddingHorizontal: 16 }}><Problem error={error} /></View> : null}
                    <TourTarget id="needs" style={{ paddingHorizontal: 20, paddingBottom: 16 }}>
                        <Press role="link" label={headline} disabled={needs.length === 0} onPress={() => router.push(routes.needs as never)}>
                            <Text accessibilityRole="header" size={30} weight="medium" style={{ lineHeight: 35, letterSpacing: -0.6 }}>
                                {pending || lemmasPending ? " " : org ? headline : " "}
                            </Text>
                        </Press>
                    </TourTarget>

                    {lemmas.length > 0 ? (
                        <TourTarget id="ask" style={{ paddingHorizontal: 16 }}>
                            <AskBox
                                lemmas={lemmas}
                                podId={chosen}
                                onPick={setAsking}
                                attach
                                onSend={(podId, text, files) => router.push(chatRoutes.start(podId, text, holdFiles(files)) as never)}
                            />
                        </TourTarget>
                    ) : null}

                    {lemmasPending ? <View style={{ padding: 20 }}><Loading label="Reading your Lemmas" /></View> : null}
                    {/* The first morning (area G): invitations, an organization to
                        join, or a new one, for somebody who has none yet. */}
                    {!orgsPending && !org ? <View style={{ paddingHorizontal: 20 }}><Arrival top={<ResumeInstall />} /></View> : null}
                    {org && !lemmasPending && lemmas.length === 0 && !error ? (
                        <Calm title={`No Lemmas at ${org.name} yet.`} line="A Lemma is a place for one part of your work: an AI you can ask, apps for the work, and the people in it.">
                            <Button title="Start a Lemma" onPress={() => router.push(moreRoutes.newLemma as never)} />
                        </Calm>
                    ) : null}

                    {lemmas.length > 0 ? (
                        <TourTarget id="lemmas" style={{ marginTop: 18 }}>
                            <View style={{ flexDirection: "row", alignItems: "center", height: 38, paddingLeft: 20, paddingRight: 10 }}>
                                <Text size={13.5} tone={color.ink3} style={{ flex: 1 }}>Lemmas</Text>
                                <IconButton name="plus" size={18} label="New Lemma" onPress={() => router.push(moreRoutes.newLemma as never)} />
                            </View>
                            {sectionsPending && sections.length === 0 ? <View style={{ paddingHorizontal: 20 }}><Loading label="Reading what moved" /></View> : null}
                            {open ? <OpenLemma section={open} apps={apps.filter((app) => app.podId === open.lemma.id)} now={now} /> : null}
                            {sections.filter((section) => section !== open).map((section) => (
                                <Press
                                    key={section.lemma.id}
                                    role="link"
                                    label={section.lemma.name + ((waitingIn.get(section.lemma.id) ?? 0) > 0 ? `, ${waitingIn.get(section.lemma.id)} waiting on you` : "")}
                                    onPress={() => router.push(routes.lemma(section.lemma.id) as never)}
                                    style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: 52, paddingHorizontal: 20 }}
                                >
                                    <LemmaFace id={section.lemma.id} icon={section.lemma.iconUrl} size={30} />
                                    <Text size={16.5} numberOfLines={1} style={{ flex: 1, minWidth: 0 }}>{section.lemma.name}</Text>
                                    <LemmaEnd section={section} waiting={waitingIn.get(section.lemma.id) ?? 0} />
                                </Press>
                            ))}
                        </TourTarget>
                    ) : null}
                </ScrollView>
            </KeyboardAvoidingView>
            <HomeOverlays org={org?.name ?? null} ready={Boolean(org) && !lemmasPending && lemmas.length > 0} />
        </SafeAreaView>
    );
}

/** The end of a Lemma's line: what waits on you, else live bars. */
function LemmaEnd({ section, waiting }: { section: LemmaSection; waiting: number }) {
    if (waiting > 0) {
        return (
            <View style={{ minWidth: 22, height: 22, paddingHorizontal: 7, borderRadius: 11, backgroundColor: color.warm, alignItems: "center", justifyContent: "center" }}>
                <Num size={12} tone={color.onWarm}>{waiting}</Num>
            </View>
        );
    }
    return section.rows.some((row) => row.running) ? <LiveBars /> : null;
}

/** The Lemma you were last in, unfolded on a lit card: its name, its apps
 *  as round marks, then its chats, three of them before "Show more". */
function OpenLemma({ section, apps, now }: { section: LemmaSection; apps: AppItem[]; now: number }) {
    const router = useRouter();
    const chats = section.rows.filter((row) => row.kind === "chat" || row.kind === "line" || row.kind === "shared");
    const { shown, hidden } = foldSection(chats, 3);
    return (
        /* Opaque paper on a hairline, lifted by a shadow on iPhone only: an
           Android elevation under a see-through card shows through it as a
           box inside the box. */
        <View
            style={{
                marginHorizontal: 12, marginTop: 2, marginBottom: 8, paddingTop: 4, paddingBottom: 8, borderRadius: 20,
                backgroundColor: color.paper, borderWidth: 1, borderColor: color.line2,
                ...(Platform.OS === "ios" ? { shadowColor: color.violet, shadowOpacity: 0.1, shadowRadius: 18, shadowOffset: { width: 0, height: 10 } } : {}),
            }}
        >
            <Press role="link" label={`Open ${section.lemma.name}`} onPress={() => router.push(routes.lemma(section.lemma.id) as never)} style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: 52, paddingHorizontal: 14 }}>
                <LemmaFace id={section.lemma.id} icon={section.lemma.iconUrl} size={30} />
                <Text size={16.5} numberOfLines={1} style={{ flex: 1, minWidth: 0 }}>{section.lemma.name}</Text>
                {section.rows.some((row) => row.running) ? <LiveBars /> : null}
                <Icon name="forward" size={16} tone={color.ink3} />
            </Press>
            {apps.length > 0 ? (
                <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 4, paddingHorizontal: 8, paddingTop: 4, paddingBottom: 12 }}>
                    {apps.map((app) => (
                        <Press key={app.id} role="link" label={`${app.label}, an app`} onPress={() => router.push(routes.app(app.podId, app.name) as never)} style={{ width: 68, alignItems: "center", gap: 5 }}>
                            <AppMark name={app.name} label={app.label} size={48} shadow />
                            <Text size={12} tone={color.ink2} numberOfLines={1}>{app.label}</Text>
                        </Press>
                    ))}
                </ScrollView>
            ) : null}
            {shown.length > 0 ? <View style={{ height: 1, marginLeft: 56, marginRight: 14, marginBottom: 2, backgroundColor: color.line }} /> : null}
            {/* One text edge for the name, the chats and "Show more": a 30pt
                glyph column under the 30pt face. */}
            {shown.map((row) => <SideRowLine key={row.key} row={row} now={now} pad={14} glyphWidth={30} />)}
            {hidden > 0 ? (
                <Press role="link" label={`Show ${hidden} more`} onPress={() => router.push(routes.lemma(section.lemma.id, "chats") as never)} style={{ minHeight: 40, justifyContent: "center", paddingLeft: 56 }}>
                    <Text size={13.5} tone={color.ink3}>Show {hidden} more</Text>
                </Press>
            ) : null}
            {shown.length === 0 ? (
                <Press role="link" label={`New chat with ${section.lemma.name}`} onPress={() => router.push(routes.newChat(section.lemma.id) as never)} style={{ minHeight: 40, justifyContent: "center", paddingLeft: 56 }}>
                    <Text size={14} tone={color.ink3}>No chats yet. Start one</Text>
                </Press>
            ) : null}
        </View>
    );
}
