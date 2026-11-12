import { useState } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { Press } from "../../ui/press";
import { Text } from "../../ui/text";
import { color } from "../../ui/tokens";
import { useFileActions } from "./actions";
import { FolderRows, FolderTools, InsideFiles, SearchField, useSearch } from "./listing";
import { isPage } from "./model";
import { GroupLabel, WriteProblem } from "./parts";
import { filesRoutes } from "./routes";

type Place = "shared" | "mine";

/** A Lemma's files and pages, on its page. Two places behind one switch:
 *  what is shared with everyone in the Lemma, and your own files (`/me`).
 *  Shared opens on its pages, then everything else; a long press on any row
 *  is where its actions are, and + adds a page, a photo, a file or a folder. */
export function FilesTab({ podId, name }: { podId: string; name: string }) {
    const router = useRouter();
    const [place, setPlace] = useState<Place>("shared");
    const directory = place === "shared" ? "/" : "/me";
    const actions = useFileActions({ podId, lemmaName: name, directory });
    const pages = useFileActions({ podId, lemmaName: name, directory: "/pages" });
    const search = useSearch();
    const now = Date.now();
    const filter = search.query;

    return (
        <View>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 6, paddingLeft: 4 }}>
                {(["shared", "mine"] as const).map((one) => (
                    <Press key={one} role="tab" label={one === "shared" ? "Shared" : "Mine"} accessibilityState={{ selected: place === one }} onPress={() => { setPlace(one); search.close(); }}
                        style={{ height: 34, paddingHorizontal: 14, borderRadius: 999, justifyContent: "center", backgroundColor: place === one ? color.ink : "transparent", borderWidth: 1, borderColor: place === one ? color.ink : color.line2 }}>
                        <Text size={14} tone={place === one ? color.onInk : color.ink}>{one === "shared" ? "Shared" : "Mine"}</Text>
                    </Press>
                ))}
                <View style={{ flex: 1 }} />
                <FolderTools onSearch={search.open} onAdd={actions.add} />
            </View>
            {search.searching ? (
                <View style={{ paddingTop: 10 }}>
                    <SearchField value={search.query} onChange={search.setQuery} onClose={search.close} placeholder={place === "shared" ? `Search ${name}’s files` : "Search your files"} />
                </View>
            ) : null}
            {place === "mine" ? <Text size={13} tone={color.ink3} style={{ paddingHorizontal: 4, paddingTop: 10 }}>Only you can open these.</Text> : null}

            <WriteProblem problem={actions.problem ?? pages.problem} onDismiss={() => { actions.clearProblem(); pages.clearProblem(); }} />
            {actions.status ?? pages.status}
            {actions.note || pages.note ? (
                <Text size={13.5} tone={color.ink2} style={{ paddingHorizontal: 4, paddingTop: 10 }} accessibilityLiveRegion="polite">{actions.note ?? pages.note}</Text>
            ) : null}

            {place === "shared" ? (
                <>
                    <GroupLabel action={{ label: "New page", onPress: pages.add }}>Pages</GroupLabel>
                    <FolderRows
                        podId={podId}
                        directory="/pages"
                        actions={pages}
                        now={now}
                        filter={filter}
                        pick={(item) => item.kind === "file" && isPage(item.path)}
                        limit={filter ? undefined : 5}
                        more={{ label: "All pages", onPress: () => router.push(filesRoutes.folder(podId, "/pages") as never) }}
                        empty="No pages yet. A page is a doc you and the Lemma both write in."
                    />
                    <GroupLabel>Files</GroupLabel>
                    <FolderRows podId={podId} directory="/" actions={actions} now={now} filter={filter} hide={["/pages", "/me"]} empty="No files yet. Add a photo or a file with +." />
                </>
            ) : (
                <View style={{ paddingTop: 6 }}>
                    <FolderRows podId={podId} directory="/me" actions={actions} now={now} filter={filter} empty="Nothing here yet. What you add here only you can open." />
                </View>
            )}
            {search.searching ? <InsideFiles podId={podId} query={search.query} within={directory} /> : null}
            {actions.sheets}
            {pages.sheets}
        </View>
    );
}
