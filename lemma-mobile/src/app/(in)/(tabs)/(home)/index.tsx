import { useMemo, useState } from "react";
import { KeyboardAvoidingView, RefreshControl, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLemmas, useMe, useNeeds, useOrg, useOwnsAll, usePulse, useStartedOnTheirOwn } from "../../../../data/hooks";
import { homeCards } from "../../../../data/model";
import { routes } from "../../../../data/routes";
import { AskBox } from "../../../../ui/ask-box";
import { chatsRoutes } from "../../../../areas/chats/routes";
import { moreRoutes } from "../../../../areas/more/routes";
import { HomeOverlays } from "../../../../areas/more/home-overlays";
import { ResumeInstall } from "../../../../areas/more/install/resume";
import { Arrival } from "../../../../areas/more/welcome/arrival";
import { TourTarget } from "../../../../areas/more/tour/target";
import { Empty, IconButton, Loading, Problem } from "../../../../ui/chrome";
import { PersonFace } from "../../../../ui/face";
import { Icon, LemmaMark } from "../../../../ui/icons";
import { NeedCard } from "../../../../ui/need-card";
import { Button, Press } from "../../../../ui/press";
import { Num, Text } from "../../../../ui/text";
import { color } from "../../../../ui/tokens";

/** Home: how many things need you, said once; how much the Lemmas did on
 *  their own; the few that need you, answerable here; and a box to ask any
 *  Lemma, pinned above the tabs. */
export default function Home() {
    const router = useRouter();
    const me = useMe();
    const { org, orgs, pending: orgsPending } = useOrg();
    const { lemmas, pending: lemmasPending, error } = useLemmas();
    const { needs, pending } = useNeeds();
    const { refetch, fetching } = usePulse();
    const owns = useOwnsAll(lemmas);
    const started = useStartedOnTheirOwn();
    const [asking, setAsking] = useState<string | null>(null);
    const now = Date.now();
    const behind = useMemo(() => new Set(lemmas.filter((one) => owns.get(one.id)?.standing === "behind").map((one) => one.id)), [lemmas, owns]);
    const cards = useMemo(() => homeCards(needs, behind), [needs, behind]);
    const first = me.data?.firstName;
    const named = first ? `, ${first}` : "";
    const headline = needs.length > 0 ? `${needs.length} ${needs.length === 1 ? "thing needs" : "things need"} you${named}.` : `Nothing needs you${named}.`;
    const chosen = asking ?? lemmas[0]?.id ?? null;

    return (
        <SafeAreaView edges={["top"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8, paddingTop: 8, paddingRight: 16, paddingLeft: 18, paddingBottom: 6 }}>
                    <LemmaMark size={20} />
                    <Press
                        label={`${org?.name ?? "Organization"}${orgs.length > 1 ? ". Change" : ""}`}
                        onPress={() => router.push(routes.account as never)}
                        style={{ flexDirection: "row", alignItems: "center", gap: 4, height: 36, paddingHorizontal: 6 }}
                    >
                        <Text size={17} weight="medium">{org?.name ?? " "}</Text>
                        {org ? <Icon name="down" size={14} width={2} tone={color.ink2} /> : null}
                    </Press>
                    <View style={{ flex: 1 }} />
                    <TourTarget id="search">
                        <IconButton name="search" label="Search" onPress={() => router.push(moreRoutes.search as never)} />
                    </TourTarget>
                    <TourTarget id="you">
                        <Press label="Your profile and settings" onPress={() => router.push(routes.account as never)} hitSlop={6}>
                            <PersonFace name={me.data?.name ?? "You"} size={34} dark />
                        </Press>
                    </TourTarget>
                </View>

                <ScrollView
                    contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 10, paddingBottom: 16, gap: 12 }}
                    refreshControl={<RefreshControl refreshing={fetching && !pending} onRefresh={() => void refetch()} tintColor={color.ink3} />}
                    keyboardShouldPersistTaps="handled"
                >
                    {error ? <Problem error={error} /> : null}
                    <TourTarget id="needs" style={{ paddingHorizontal: 4, gap: 8 }}>
                        <Text accessibilityRole="header" size={28} weight="medium" style={{ lineHeight: 32, letterSpacing: -0.56 }}>
                            {pending || lemmasPending ? " " : org ? headline : " "}
                        </Text>
                        {started.count > 0 ? (
                            <Text size={15} tone={color.ink2} style={{ lineHeight: 21.75 }}>
                                Your Lemmas started <Num size={15} tone={color.ink2}>{started.count}</Num> {started.count === 1 ? "run" : "runs"} on their own {started.since}.
                            </Text>
                        ) : null}
                    </TourTarget>

                    {pending || lemmasPending ? <Loading label="Reading what needs you" /> : null}
                    {/* The first morning (area G): invitations, an organization to
                        join, or a new one, for somebody who has none yet. */}
                    {!orgsPending && !org ? <View style={{ paddingHorizontal: 4 }}><Arrival top={<ResumeInstall />} /></View> : null}
                    {org && !lemmasPending && lemmas.length === 0 && !error ? (
                        <Empty title={`No Lemmas at ${org?.name ?? "this organization"} yet.`}>
                            <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>Give one something to own, and it builds and runs what it takes.</Text>
                            <View style={{ flexDirection: "row", marginTop: 8 }}>
                                <Button title="Start a Lemma" onPress={() => router.push(moreRoutes.newLemma as never)} />
                            </View>
                        </Empty>
                    ) : null}

                    <TourTarget id="needs" style={{ gap: 10, marginTop: 6 }}>
                        <View accessibilityLabel="Needs you" style={{ gap: 10 }}>
                            {cards.map((item) => (
                                <NeedCard key={item.key} item={item} lemma={lemmas.find((one) => one.id === item.podId) ?? null} behind={behind.has(item.podId)} now={now} />
                            ))}
                        </View>
                    </TourTarget>
                    {needs.length > cards.length ? (
                        <Press role="link" label={`See all ${needs.length}`} onPress={() => router.push(routes.needs as never)} style={{ alignSelf: "flex-start", paddingVertical: 10, paddingHorizontal: 4 }}>
                            <Text size={14} tone={color.ink2}>See all <Num size={14} tone={color.ink2}>{needs.length}</Num></Text>
                        </Press>
                    ) : null}
                    {lemmas.length > 0 ? (
                        <Press role="link" label="All chats" onPress={() => router.push(chatsRoutes.all as never)} style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 48, marginTop: 10, paddingHorizontal: 4, borderTopWidth: 1, borderTopColor: color.line }}>
                            <Icon name="chat" size={18} tone={color.ink2} />
                            <Text size={15.5} style={{ flex: 1 }}>All chats</Text>
                            <Icon name="forward" size={16} tone={color.ink3} />
                        </Press>
                    ) : null}
                </ScrollView>

                {lemmas.length > 0 ? (
                    <View style={{ paddingHorizontal: 12, paddingTop: 8, paddingBottom: 10, backgroundColor: color.paper, borderTopWidth: 1, borderTopColor: color.line }}>
                        <TourTarget id="ask">
                        <AskBox
                            lemmas={lemmas}
                            podId={chosen}
                            onPick={setAsking}
                            onSend={(podId, text) => router.push(routes.chat(podId, "new", text) as never)}
                        />
                        </TourTarget>
                    </View>
                ) : null}
            </KeyboardAvoidingView>
            <HomeOverlays org={org?.name ?? null} ready={Boolean(org) && !lemmasPending && lemmas.length > 0} />
        </SafeAreaView>
    );
}
