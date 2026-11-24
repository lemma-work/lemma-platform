import { useMemo, useState } from "react";
import { RefreshControl, ScrollView, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { useApps, useLemmas } from "../../../data/hooks";
import { routes } from "../../../data/routes";
import type { AppItem } from "../../../data/types";
import { AppTile } from "../../../ui/app-tile";
import { Calm } from "../../../ui/calm";
import { IconButton, Loading } from "../../../ui/chrome";
import { LemmaFace } from "../../../ui/face";
import { Icon } from "../../../ui/icons";
import { MeButton } from "../../../ui/me-button";
import { Button, Press } from "../../../ui/press";
import { Num, Text } from "../../../ui/text";
import { color, font } from "../../../ui/tokens";

/** Apps, as the web's page (`outcomes/apps.tsx`): every app across the
 *  organization's Lemmas, newest first, two to a row with their covers;
 *  a chip per Lemma when more than one has apps, and a search. Adding one
 *  starts a chat with the Lemma that should build it, as "Describe your
 *  own" does on the web. */
export default function Apps() {
    const router = useRouter();
    const { lemmas, org } = useLemmas();
    const { apps, pending, refetch } = useApps();
    const [only, setOnly] = useState<string | null>(null);
    const [find, setFind] = useState("");
    const now = Date.now();
    const newest = useMemo(() => [...apps].sort((a, b) => (Date.parse(b.updatedAt ?? "") || 0) - (Date.parse(a.updatedAt ?? "") || 0)), [apps]);
    const holding = lemmas.filter((lemma) => apps.some((app) => app.podId === lemma.id));
    const words = find.trim().toLowerCase();
    const shown = newest.filter((app) => (!only || app.podId === only) && (!words || (app.label + " " + app.name + " " + app.podName).toLowerCase().includes(words)));
    const add = () => {
        const into = only ?? holding[0]?.id ?? lemmas[0]?.id;
        if (into) router.push(routes.newChat(into) as never);
    };
    const rows: AppItem[][] = [];
    for (let index = 0; index < shown.length; index += 2) rows.push(shown.slice(index, index + 2));

    return (
        <SafeAreaView edges={["top"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <View style={{ flexDirection: "row", justifyContent: "flex-end", alignItems: "center", gap: 6, paddingTop: 6, paddingHorizontal: 14 }}>
                {lemmas.length > 0 ? <IconButton name="plus" size={22} label="Add an app" onPress={add} /> : null}
                <MeButton />
            </View>
            <ScrollView
                contentContainerStyle={{ paddingBottom: 32 }}
                keyboardShouldPersistTaps="handled"
                refreshControl={<RefreshControl refreshing={false} onRefresh={() => void refetch()} tintColor={color.ink3} />}
            >
                <View style={{ paddingHorizontal: 20, paddingBottom: 14 }}>
                    <Text accessibilityRole="header" size={30} weight="medium" style={{ letterSpacing: -0.6, lineHeight: 35 }}>Apps</Text>
                    {!pending && apps.length > 0 ? (
                        <Text size={14.5} tone={color.ink2} style={{ marginTop: 4 }}>
                            {`${apps.length} ${apps.length === 1 ? "app" : "apps"} across ${org?.name ? org.name + "’s" : "your"} ${holding.length} ${holding.length === 1 ? "Lemma" : "Lemmas"}, newest first.`}
                        </Text>
                    ) : null}
                </View>

                {holding.length > 1 ? (
                    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingHorizontal: 16 }}>
                        <Chip on={only === null} label="All" count={apps.length} onPress={() => setOnly(null)} />
                        {holding.map((lemma) => (
                            <Chip key={lemma.id} on={only === lemma.id} label={lemma.name} count={apps.filter((app) => app.podId === lemma.id).length} onPress={() => setOnly(only === lemma.id ? null : lemma.id)} face={{ id: lemma.id, icon: lemma.iconUrl }} />
                        ))}
                    </ScrollView>
                ) : null}
                {apps.length > 3 ? (
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 8, height: 42, marginHorizontal: 16, marginTop: 12, paddingHorizontal: 12, borderRadius: 12, backgroundColor: color.wash }}>
                        <Icon name="search" size={18} tone={color.ink3} />
                        <TextInput
                            value={find}
                            onChangeText={setFind}
                            placeholder="Find an app"
                            placeholderTextColor={color.ink3}
                            accessibilityLabel="Find an app"
                            style={{ flex: 1, fontFamily: font.regular, fontSize: 16, color: color.ink, paddingVertical: 0 }}
                        />
                    </View>
                ) : null}

                <View style={{ paddingHorizontal: 16, paddingTop: 16, gap: 18 }}>
                    {pending ? <Loading label="Reading your apps" /> : null}
                    {!pending && apps.length === 0 ? (
                        <Calm faces={lemmas.slice(0, 3)} title="No apps yet" line="Describe one, and a Lemma builds it. It shows here, and around that Lemma’s face.">
                            {lemmas.length > 0 ? <Button title="Describe your own" onPress={add} /> : null}
                        </Calm>
                    ) : null}
                    {!pending && apps.length > 0 && shown.length === 0 ? <Text size={15} tone={color.ink2}>No app here matches “{find.trim()}”.</Text> : null}
                    {rows.map((row, index) => (
                        <View key={index} style={{ flexDirection: "row", gap: 12 }}>
                            {row.map((app) => <AppTile key={app.id} app={app} by now={now} />)}
                            {row.length === 1 ? <View style={{ flex: 1 }} /> : null}
                        </View>
                    ))}
                </View>
            </ScrollView>
        </SafeAreaView>
    );
}

function Chip({ on, label, count, onPress, face }: { on: boolean; label: string; count: number; onPress: () => void; face?: { id: string; icon: string | null } }) {
    return (
        <Press
            label={`${label}, ${count}`}
            accessibilityState={{ selected: on }}
            onPress={onPress}
            style={{ height: 34, paddingLeft: face ? 5 : 13, paddingRight: 13, borderRadius: 999, borderWidth: 1, borderColor: on ? color.info : color.line2, backgroundColor: on ? color.pick : color.paper, flexDirection: "row", alignItems: "center", gap: 6 }}
        >
            {face ? <LemmaFace id={face.id} icon={face.icon} size={24} /> : null}
            <Text size={14} tone={on ? color.info : color.ink}>{label}</Text>
            <Num size={12.5} tone={on ? color.info : color.ink3}>{count}</Num>
        </Press>
    );
}
