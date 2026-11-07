import { KeyboardAvoidingView, ScrollView, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { SafeAreaView, useSafeAreaInsets } from "react-native-safe-area-context";
import { useLemma, useLemmas, useOwns, useRuns, useStarters } from "../../../data/hooks";
import { NOTHING_OWNED } from "../../../data/model";
import { chatRoutes } from "../../../areas/chat/routes";
import { routes, workspace } from "../../../data/routes";
import type { Starter } from "../../../data/types";
import { AppTile } from "../../../ui/app-tile";
import { holdFiles } from "../../../thread/hand-over";
import { AskBox } from "../../../ui/ask-box";
import { Loading } from "../../../ui/chrome";
import { LemmaFace } from "../../../ui/face";
import { Icon, type IconName } from "../../../ui/icons";
import { useIdentity } from "../../../ui/identity";
import { Press } from "../../../ui/press";
import { Text } from "../../../ui/text";
import { color, TOUCH } from "../../../ui/tokens";

const STARTER_ICON: Record<Starter["platform"], IconName | "#"> = { GROUP: "people", WHATSAPP: "whatsapp", SLACK: "#", TELEGRAM: "telegram" };

/** A chat not started yet: who it is with and what it owns, what it runs,
 *  ways to start one with people instead, and the box -- where the Lemma is
 *  picked. Nothing is created until the first send. The Lemma's colour is
 *  here already: its wash behind the top, its ink on Send and the links. */
export default function NewChat() {
    const router = useRouter();
    const params = useLocalSearchParams<{ podId: string }>();
    const podId = String(params.podId);
    const insets = useSafeAreaInsets();
    const identity = useIdentity(podId);
    const lemma = useLemma(podId);
    const { lemmas } = useLemmas();
    const owns = useOwns(lemma);
    const runs = useRuns(lemma);
    const starters = useStarters(lemma);
    const chips = [
        ...(runs.data?.tables ?? []).slice(0, 4).map((table) => ({ key: "t:" + table.name, icon: "table" as const, label: table.label, tail: workspace.table(podId, table.name) })),
        ...(runs.data?.automations ?? []).slice(0, 3).map((flow) => ({ key: "a:" + flow.name, icon: "automation" as const, label: flow.label, tail: `/t/${encodeURIComponent(podId)}/workflow/${encodeURIComponent(flow.name)}` })),
    ];
    return (
        <SafeAreaView edges={["bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
                <View style={{ flexDirection: "row", alignItems: "center", paddingTop: insets.top + 4, paddingBottom: 4, paddingHorizontal: 8, backgroundColor: identity.wash }}>
                    <Press label="Cancel" onPress={() => (router.canGoBack() ? router.back() : router.replace("/" as never))} style={{ height: TOUCH, paddingHorizontal: 10, justifyContent: "center", width: 80 }}>
                        <Text size={16}>Cancel</Text>
                    </Press>
                    <View style={{ flex: 1, alignItems: "center" }}><Text accessibilityRole="header" size={16} weight="medium">New chat</Text></View>
                    <View style={{ width: 80 }} />
                </View>
                {!lemma ? <Loading /> : (
                    <ScrollView contentContainerStyle={{ paddingTop: 14, paddingHorizontal: 20, paddingBottom: 20, gap: 22 }} keyboardShouldPersistTaps="handled">
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 14 }}>
                            <LemmaFace id={lemma.id} icon={lemma.iconUrl} size={52} />
                            <View style={{ flex: 1, gap: 3 }}>
                                <Text size={20} weight="medium">{lemma.name}</Text>
                                <Text size={14.5} tone={color.ink2} style={{ lineHeight: 19.6 }}>{owns.data?.sentence ? `Owns: ${owns.data.sentence}.` : owns.error ? " " : NOTHING_OWNED}</Text>
                            </View>
                        </View>

                        {(runs.data?.apps.length ?? 0) > 0 || chips.length > 0 ? (
                            <View accessibilityLabel="What it runs" style={{ gap: 10 }}>
                                <Text size={12.5} tone={color.ink3}>What it runs</Text>
                                {(runs.data?.apps.length ?? 0) > 0 ? (
                                    <View style={{ flexDirection: "row", gap: 10 }}>
                                        {runs.data!.apps.slice(0, 2).map((app) => <AppTile key={app.id} app={app} height={86} />)}
                                        {runs.data!.apps.length === 1 ? <View style={{ flex: 1 }} /> : null}
                                    </View>
                                ) : null}
                                {chips.length > 0 ? (
                                    <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6 }}>
                                        {chips.map((chip) => (
                                            <Press key={chip.key} role="link" label={chip.label} onPress={() => router.push(routes.web(podId, chip.tail, chip.label) as never)} hitSlop={{ top: 6, bottom: 6 }} style={{ flexDirection: "row", alignItems: "center", gap: 6, height: 32, paddingHorizontal: 10, borderWidth: 1, borderColor: color.line2, borderRadius: 999 }}>
                                                <Icon name={chip.icon} size={14} tone={color.ink2} />
                                                <Text size={13.5}>{chip.label}</Text>
                                            </Press>
                                        ))}
                                    </View>
                                ) : null}
                            </View>
                        ) : runs.isPending ? <Loading /> : null}

                        <View accessibilityLabel="Or with people">
                            <Text size={12.5} tone={color.ink3} style={{ marginBottom: 4 }}>Or with people</Text>
                            {(starters.data ?? []).map((starter, index, all) => {
                                const glyph = STARTER_ICON[starter.platform];
                                return (
                                    <Press
                                        key={starter.platform}
                                        role="link"
                                        label={starter.label + (starter.ready ? "" : ". Connect")}
                                        onPress={() => router.push(routes.web(podId, starter.ready ? `${workspace.pod(podId)}/chats` : `${workspace.pod(podId)}/about`, starter.label) as never)}
                                        style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: 48, borderBottomWidth: index < all.length - 1 ? 1 : 0, borderBottomColor: color.line }}
                                    >
                                        <View style={{ width: 32, height: 32, borderRadius: 16, backgroundColor: color.wash, alignItems: "center", justifyContent: "center" }}>
                                            {glyph === "#" ? <Text size={16}>#</Text> : <Icon name={glyph} size={17} />}
                                        </View>
                                        <Text size={15.5} style={{ flex: 1 }}>{starter.label}</Text>
                                        {starter.ready ? (starter.note ? <Text size={13} tone={color.ink3}>{starter.note}</Text> : null) : <Text size={13.5} tone={identity.ink}>Connect</Text>}
                                    </Press>
                                );
                            })}
                        </View>
                    </ScrollView>
                )}
                {lemma ? (
                    <View style={{ paddingHorizontal: 12, paddingTop: 8, paddingBottom: 10 }}>
                        <AskBox
                            lemmas={lemmas}
                            podId={podId}
                            attach
                            autoFocus={false}
                            placeholder={`Ask ${lemma.name}, or say what to do`}
                            onPick={(next) => router.replace(routes.newChat(next) as never)}
                            onSend={(chosen, text, files) => router.replace(chatRoutes.start(chosen, text, holdFiles(files)) as never)}
                        />
                    </View>
                ) : null}
            </KeyboardAvoidingView>
        </SafeAreaView>
    );
}
