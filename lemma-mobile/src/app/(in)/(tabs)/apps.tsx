import { RefreshControl, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { useApps, useLemmas } from "../../../data/hooks";
import { routes } from "../../../data/routes";
import type { AppItem, Lemma } from "../../../data/types";
import { AppTile } from "../../../ui/app-tile";
import { Empty, IconButton, Loading, PageTitle } from "../../../ui/chrome";
import { LemmaFace } from "../../../ui/face";
import { Press } from "../../../ui/press";
import { Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";

/** Every app, shelved by the Lemma that runs it. A Lemma with one app takes
 *  half a row, so two of them sit side by side. */
export default function Apps() {
    const router = useRouter();
    const { lemmas } = useLemmas();
    const { apps, pending, refetch } = useApps();
    const shelves = lemmas
        .map((lemma) => ({ lemma, apps: apps.filter((app) => app.podId === lemma.id) }))
        .filter((shelf) => shelf.apps.length > 0);
    /* Pair up the shelves of one app, in order. */
    const rows: ({ kind: "full"; shelf: (typeof shelves)[number] } | { kind: "pair"; shelves: (typeof shelves)[number][] })[] = [];
    for (const shelf of shelves) {
        const last = rows[rows.length - 1];
        if (shelf.apps.length === 1 && last?.kind === "pair" && last.shelves.length === 1) last.shelves.push(shelf);
        else if (shelf.apps.length === 1) rows.push({ kind: "pair", shelves: [shelf] });
        else rows.push({ kind: "full", shelf });
    }
    return (
        <SafeAreaView edges={["top"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <View style={{ paddingTop: 12 }}>
                <PageTitle right={lemmas[0] ? <IconButton name="plus" size={22} label="Ask a Lemma for a new app" onPress={() => router.push(routes.newChat(lemmas[0].id) as never)} /> : null}>Apps</PageTitle>
            </View>
            <ScrollView
                contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 8, paddingBottom: 24, gap: 22 }}
                refreshControl={<RefreshControl refreshing={false} onRefresh={() => void refetch()} tintColor={color.ink3} />}
            >
                {pending ? <Loading /> : null}
                {!pending && shelves.length === 0 ? (
                    <Empty title="No apps yet.">
                        <Text size={15} tone={color.ink2}>Ask a Lemma to build one, and it shows here.</Text>
                    </Empty>
                ) : null}
                {rows.map((row, index) => row.kind === "full" ? (
                    <Shelf key={row.shelf.lemma.id} lemma={row.shelf.lemma} apps={row.shelf.apps} />
                ) : (
                    <View key={"pair" + index} style={{ flexDirection: "row", gap: 12 }}>
                        {row.shelves.map((shelf) => (
                            <View key={shelf.lemma.id} style={{ flex: 1 }}>
                                <Shelf lemma={shelf.lemma} apps={shelf.apps} />
                            </View>
                        ))}
                        {row.shelves.length === 1 ? <View style={{ flex: 1 }} /> : null}
                    </View>
                ))}
            </ScrollView>
        </SafeAreaView>
    );
}

function Shelf({ lemma, apps }: { lemma: Lemma; apps: AppItem[] }) {
    const router = useRouter();
    const pairs: AppItem[][] = [];
    for (let index = 0; index < apps.length; index += 2) pairs.push(apps.slice(index, index + 2));
    return (
        <View accessibilityLabel={lemma.name} style={{ gap: 10 }}>
            <Press role="link" label={lemma.name} onPress={() => router.push(routes.lemma(lemma.id) as never)} style={{ flexDirection: "row", alignItems: "center", gap: 8, minHeight: 32, alignSelf: "flex-start" }}>
                <LemmaFace id={lemma.id} icon={lemma.iconUrl} size={22} />
                <Text size={15} weight="medium">{lemma.name}</Text>
            </Press>
            {pairs.map((pair, index) => (
                <View key={index} style={{ flexDirection: "row", gap: 12 }}>
                    {pair.map((app) => <AppTile key={app.id} app={app} />)}
                    {pair.length === 1 && apps.length > 1 ? <View style={{ flex: 1 }} /> : null}
                </View>
            ))}
        </View>
    );
}
