import { useState } from "react";
import { ActivityIndicator, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { Problem } from "../../ui/chrome";
import { Icon } from "../../ui/icons";
import { Press } from "../../ui/press";
import { Text } from "../../ui/text";
import { color, font, TOUCH } from "../../ui/tokens";
import type { useFileActions } from "./actions";
import { FilesIcon } from "./glyphs";
import { useFileSearch, useFolder } from "./hooks";
import { docTitle } from "@web/library/doc-title";
import { isInternal, isMissingFolder, isPage, nameOf, ordered, parentOf, titleOf } from "./model";
import { FileRow, GroupLabel } from "./parts";
import { filesRoutes } from "./routes";
import type { LibraryItem } from "./source";

type Actions = ReturnType<typeof useFileActions>;

/** A folder's rows, folders first and then what changed last, with the
 *  paging the server does said as "More". `filter` narrows what is loaded by
 *  name; `pick` narrows by kind; `limit` shows the first few with a way on. */
export function FolderRows({ podId, directory, actions, now, filter = "", pick, hide = [], limit, more, empty }: {
    podId: string;
    directory: string;
    actions: Actions;
    now: number;
    filter?: string;
    pick?: (item: LibraryItem) => boolean;
    hide?: string[];
    limit?: number;
    more?: { label: string; onPress: () => void };
    empty?: string;
}) {
    const folder = useFolder(podId, directory);
    const needle = filter.trim().toLowerCase();
    const all = ordered((folder.data?.pages ?? []).flatMap((page) => page.items))
        .filter((item) => !isInternal(item) && !hide.includes(item.path) && (!pick || pick(item)))
        .filter((item) => !needle || `${item.name} ${titleOf(item)}`.toLowerCase().includes(needle));
    const shown = limit ? all.slice(0, limit) : all;
    /* A folder nobody has made yet (no pages so far) is an empty one. The
       server says so with a 404, or a 400 "Directory not found". */
    const missing = isMissingFolder(folder.error);
    if (folder.isPending) return <ActivityIndicator color={color.ink3} style={{ paddingVertical: 14 }} />;
    if (folder.isError && !missing) return <Problem error={folder.error} onRetry={() => void folder.refetch()} />;
    return (
        <View>
            {shown.length === 0 ? (
                <Text size={15} tone={color.ink2} style={{ paddingHorizontal: 4, paddingVertical: 10 }}>
                    {needle ? "Nothing here by that name." : empty ?? "Nothing here yet."}
                </Text>
            ) : null}
            {shown.map((item) => (
                <FileRow key={item.path} item={item} now={now} busy={actions.writes.busy === item.path} onPress={() => actions.open(item)} onLongPress={() => actions.hold(item)} />
            ))}
            {(limit && all.length > limit) || folder.hasNextPage ? (
                <Press
                    role="link"
                    label={more && all.length > (limit ?? Infinity) ? more.label : "More"}
                    onPress={() => (more && all.length > (limit ?? Infinity) ? more.onPress() : void folder.fetchNextPage())}
                    style={{ minHeight: 40, justifyContent: "center", paddingHorizontal: 4, alignSelf: "flex-start" }}
                >
                    {folder.isFetchingNextPage ? <ActivityIndicator size="small" color={color.ink3} /> : (
                        <Text size={13.5} tone={color.ink3}>{more && all.length > (limit ?? Infinity) ? more.label : "More"}</Text>
                    )}
                </Press>
            ) : null}
        </View>
    );
}

/** The search box: names as you type, from what is loaded, and what is
 *  inside files once the server has read them. */
export function SearchField({ value, onChange, onClose, placeholder }: { value: string; onChange: (text: string) => void; onClose: () => void; placeholder: string }) {
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8, height: 42, paddingLeft: 12, paddingRight: 4, borderRadius: 12, backgroundColor: color.wash }}>
            <Icon name="search" size={17} tone={color.ink3} />
            <TextInput
                accessibilityLabel="Search files"
                value={value}
                onChangeText={onChange}
                placeholder={placeholder}
                placeholderTextColor={color.ink3}
                autoFocus
                autoCorrect={false}
                returnKeyType="search"
                style={{ flex: 1, fontFamily: font.regular, fontSize: 15.5, color: color.ink, paddingVertical: 0 }}
            />
            <Press label="Close search" onPress={onClose} style={{ width: 36, height: 36, alignItems: "center", justifyContent: "center" }}>
                <Icon name="close" size={17} tone={color.ink2} />
            </Press>
        </View>
    );
}

/** What is inside the files, from the server's search, one row per file
 *  with the passage that matched. */
export function InsideFiles({ podId, query, within }: { podId: string; query: string; within: string }) {
    const router = useRouter();
    const found = useFileSearch(podId, query, within);
    if (query.trim().length < 2) return null;
    return (
        <View>
            <GroupLabel>Inside files</GroupLabel>
            {found.isPending && found.fetchStatus !== "idle" ? <ActivityIndicator color={color.ink3} style={{ paddingVertical: 10 }} /> : null}
            {found.isError ? <Text size={14} tone={color.amber} style={{ paddingHorizontal: 4 }}>Search did not answer just now.</Text> : null}
            {found.data && found.data.length === 0 ? <Text size={15} tone={color.ink2} style={{ paddingHorizontal: 4, paddingVertical: 8 }}>Nothing inside the files says that.</Text> : null}
            {(found.data ?? []).map((hit) => (
                <Press key={hit.path} role="link" label={`${docTitle(nameOf(hit.path))}: ${hit.snippet}`} onPress={() => router.push(filesRoutes.file(podId, hit.path) as never)} style={{ flexDirection: "row", gap: 12, paddingVertical: 9, paddingHorizontal: 4, minHeight: TOUCH }}>
                    <View style={{ width: 22, alignItems: "center", paddingTop: 1 }}>
                        <FilesIcon name={isPage(hit.path) ? "page" : "file"} size={19} />
                    </View>
                    <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                        <Text size={15.5} numberOfLines={1}>{docTitle(nameOf(hit.path))}</Text>
                        <Text size={13} tone={color.ink3} numberOfLines={2}>{hit.snippet}</Text>
                        <Text size={12} tone={color.ink3} numberOfLines={1}>{parentOf(hit.path)}{hit.page ? ` · page ${hit.page}` : ""}</Text>
                    </View>
                </Press>
            ))}
        </View>
    );
}

/** The two buttons a files screen keeps at its top right. */
export function FolderTools({ onSearch, onAdd }: { onSearch: () => void; onAdd: () => void }) {
    return (
        <View style={{ flexDirection: "row", alignItems: "center" }}>
            <Press label="Search files" onPress={onSearch} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                <Icon name="search" size={20} />
            </Press>
            <Press label="Add" onPress={onAdd} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                <Icon name="plus" size={21} />
            </Press>
        </View>
    );
}

export function useSearch() {
    const [searching, setSearching] = useState(false);
    const [query, setQuery] = useState("");
    return { searching, query, setQuery, open: () => setSearching(true), close: () => { setSearching(false); setQuery(""); } };
}
