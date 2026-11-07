import { ScrollView, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLemma } from "../../../../data/hooks";
import { useFileActions } from "../../../../areas/files/actions";
import { FolderRows, FolderTools, InsideFiles, SearchField, useSearch } from "../../../../areas/files/listing";
import { isPersonal, nameOf, parentOf } from "../../../../areas/files/model";
import { Trail, WriteProblem } from "../../../../areas/files/parts";
import { filesRoutes } from "../../../../areas/files/routes";
import { BackLink } from "../../../../ui/chrome";
import { Text } from "../../../../ui/text";
import { color } from "../../../../ui/tokens";

/** One folder of a Lemma's files: its rows, the way back up, and the same +
 *  and long press as the Files tab. `?path=` names the folder. */
export default function FolderScreen() {
    const router = useRouter();
    const params = useLocalSearchParams<{ podId: string; path?: string }>();
    const podId = String(params.podId);
    const path = String(params.path ?? "/") || "/";
    const lemma = useLemma(podId);
    const lemmaName = lemma?.name ?? "the Lemma";
    const actions = useFileActions({ podId, lemmaName, directory: path });
    const search = useSearch();
    const root = isPersonal(path) ? "/me" : "/";
    const up = parentOf(path);
    const title = path === "/me" ? "Mine" : path === "/pages" ? "Pages" : nameOf(path);
    const now = Date.now();

    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <View style={{ flexDirection: "row", alignItems: "center", paddingTop: 4, paddingLeft: 6, paddingRight: 8 }}>
                <BackLink
                    label={up === "/" || path === "/me" ? "Files" : nameOf(up)}
                    onPress={() => (router.canGoBack() ? router.back() : router.replace(filesRoutes.files(podId) as never))}
                />
                <View style={{ flex: 1 }} />
                <FolderTools onSearch={search.open} onAdd={actions.add} />
            </View>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 32 }} keyboardShouldPersistTaps="handled">
                <View style={{ paddingHorizontal: 4, paddingTop: 4, paddingBottom: 6, gap: 2 }}>
                    <Text accessibilityRole="header" size={24} weight="medium" numberOfLines={2} style={{ letterSpacing: -0.24 }}>{title}</Text>
                    {path !== root ? (
                        <Trail path={path} root={root} onGo={(to) => router.push((to === "/" ? filesRoutes.files(podId) : filesRoutes.folder(podId, to)) as never)} />
                    ) : null}
                </View>
                {search.searching ? (
                    <View style={{ paddingVertical: 6 }}>
                        <SearchField value={search.query} onChange={search.setQuery} onClose={search.close} placeholder={`Search ${title}`} />
                    </View>
                ) : null}
                <WriteProblem problem={actions.problem} onDismiss={actions.clearProblem} />
                {actions.status}
                {actions.note ? <Text size={13.5} tone={color.ink2} style={{ paddingHorizontal: 4, paddingTop: 6 }} accessibilityLiveRegion="polite">{actions.note}</Text> : null}
                <FolderRows podId={podId} directory={path} actions={actions} now={now} filter={search.query} empty="This folder is empty. Add something with +." />
                {search.searching ? <InsideFiles podId={podId} query={search.query} within={path} /> : null}
            </ScrollView>
            {actions.sheets}
        </SafeAreaView>
    );
}
