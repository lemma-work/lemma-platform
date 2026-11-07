import { useState } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { stampOf } from "../../data/model";
import { NEW_CHAT, routes } from "../../data/routes";
import { Empty, Loading, Problem as Failed } from "../../ui/chrome";
import { Icon } from "../../ui/icons";
import { Button, Press } from "../../ui/press";
import { Num, Text } from "../../ui/text";
import { color, TOUCH } from "../../ui/tokens";
import { AskSheet, Chip, Quiet, SearchBox } from "./bits";
import { useTableList } from "./hooks";
import { tablesRoutes } from "./routes";
import type { TableAccess } from "./source";
import { readableName } from "@web/library/reading";

/** Past this many tables, a search box above them. */
const SEARCH_FROM = 8;

/** Who can open a table, said only when it is not simply everyone here
 *  (the web's `all-view.tsx` access column). */
const ACCESS: Record<TableAccess, string | null> = {
    space: null,
    rows: "Own rows each",
    personal: "Only you",
    restricted: "Some people",
    public: "Anyone with the link",
};

/** Two tables people start with, as the web's empty Tables place offers them. */
const STARTERS = [
    { label: "Tasks", text: "Set up a table for our tasks, with a title, an owner, a due date and a status." },
    { label: "Contacts", text: "Set up a table for the people we work with: name, company, email and notes." },
];

/** A Lemma's tables, on its page: one line each, its name as a person would
 *  write it, who can open it when that is not everyone, and when it last
 *  changed. A new table is the Lemma's to set up, so "New table" asks it. */
export function TablesTab({ podId, name }: { podId: string; name: string }) {
    const router = useRouter();
    const list = useTableList(podId);
    const [search, setSearch] = useState("");
    const [asking, setAsking] = useState<string | null>(null);
    const now = Date.now();
    const tables = list.data?.pages.flatMap((page) => page.items) ?? [];
    const wanted = search.trim().toLowerCase();
    const shown = wanted ? tables.filter((table) => `${table.name} ${readableName(table.name)}`.toLowerCase().includes(wanted)) : tables;
    const ask = <AskSheet open={asking !== null} onClose={() => setAsking(null)} name={name} start={asking ?? ""} onSend={(text) => router.push(routes.chat(podId, NEW_CHAT, text) as never)} />;

    if (list.isPending) return <Loading label="Reading its tables" />;
    if (list.isError) return <Failed error={list.error} onRetry={() => void list.refetch()} />;
    if (tables.length === 0) {
        return (
            <>
                <Empty title="No tables yet">
                    <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>Tables keep what your team tracks, one row each; {name} sets them up and fills them in.</Text>
                    <View style={{ flexDirection: "row", paddingTop: 4 }}>
                        <Button title={`Ask ${name} to set one up`} onPress={() => setAsking("Set up a table to track ")} />
                    </View>
                    <View style={{ flexDirection: "row", gap: 8, paddingTop: 4 }}>
                        {STARTERS.map((starter) => <Chip key={starter.label} text={starter.label} on={false} label={`Ask for a ${starter.label} table`} onPress={() => setAsking(starter.text)} />)}
                    </View>
                </Empty>
                {ask}
            </>
        );
    }
    return (
        <View>
            {tables.length > SEARCH_FROM ? <View style={{ paddingBottom: 6 }}><SearchBox value={search} onChange={setSearch} placeholder="Search tables" /></View> : null}
            {shown.map((table) => {
                const at = table.updatedAt ? Date.parse(table.updatedAt) : NaN;
                const title = readableName(table.name);
                const access = ACCESS[table.access];
                return (
                    <Press
                        key={table.name}
                        role="link"
                        label={`${title}${access ? `, ${access.toLowerCase()}` : ""}`}
                        onPress={() => router.push(tablesRoutes.table(podId, table.name) as never)}
                        style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: TOUCH + 4, paddingHorizontal: 4, borderBottomWidth: 1, borderBottomColor: color.line }}
                    >
                        <Icon name="table" size={17} tone={color.ink2} />
                        <Text size={15.5} numberOfLines={1} style={{ flex: 1, minWidth: 0 }}>
                            {title}
                            {access ? <Text size={13.5} tone={color.ink3}>{"  "}{access}</Text> : null}
                        </Text>
                        {Number.isFinite(at) ? <Num size={13} tone={color.ink3}>{stampOf(at, now)}</Num> : null}
                    </Press>
                );
            })}
            {wanted && shown.length === 0 ? <Text size={15} tone={color.ink2} style={{ paddingHorizontal: 4, paddingVertical: 12 }}>No table here is called that.</Text> : null}
            {list.hasNextPage ? <Quiet text={list.isFetchingNextPage ? "Loading…" : "More tables"} onPress={() => { if (!list.isFetchingNextPage) void list.fetchNextPage(); }} tone={color.ink} /> : null}
            <View style={{ paddingTop: 6 }}>
                <Quiet text="New table" label={`Ask ${name} to set up a table`} onPress={() => setAsking("Set up a table to track ")} />
            </View>
            {ask}
        </View>
    );
}
