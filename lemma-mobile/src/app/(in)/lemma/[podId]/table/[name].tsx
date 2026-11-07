import { useMemo, useState } from "react";
import { RefreshControl, ScrollView, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLemma } from "../../../../../data/hooks";
import { NEW_CHAT, routes } from "../../../../../data/routes";
import { useFeature } from "../../../../../session/features";
import { BackLink, Empty, IconButton, Loading, Problem as Failed } from "../../../../../ui/chrome";
import { ShareSheet } from "../../../../../areas/more/share/sheet";
import { FilesIcon } from "../../../../../areas/files/glyphs";
import { Button, Press } from "../../../../../ui/press";
import { Text } from "../../../../../ui/text";
import { color, TOUCH } from "../../../../../ui/tokens";
import { AskChips, ColumnsSheet } from "../../../../../areas/tables/ask";
import { ActionSheet, AskSheet, ConfirmSheet, FixedValues, Problem, Quiet, SearchBox, type Action } from "../../../../../areas/tables/bits";
import { useRowWrites, useRows, useShape } from "../../../../../areas/tables/hooks";
import { countLine, fixedValues, readTable, WHOLE } from "../../../../../areas/tables/layouts";
import { tablesRoutes } from "../../../../../areas/tables/routes";
import { Layout, PlainRows } from "../../../../../areas/tables/views";
import { isAsking, NO_FILTERS, type Filters, type Ordering } from "@web/library/filters";
import { FORM_NAMES } from "@web/library/profile";
import { readableName } from "@web/library/reading";
import { idOf, rowLabel, type Row } from "@web/library/record-cache";

/** A table, in the layout its rows ask for (`lemma-frontend` `TableView`):
 *  a board, a checklist, a timeline, cards, a reading view or a chart, or
 *  plain rows. Search, filters and sort narrow it as one question; the count
 *  at the foot says which of three things is true. Ticking an item and
 *  moving a card happen where they are; holding a row offers the rest. Live:
 *  another person's change arrives over the pod's socket. */
export default function TableScreen() {
    const router = useRouter();
    const params = useLocalSearchParams<{ podId: string; name: string; back?: string }>();
    const podId = String(params.podId);
    const name = String(params.name);
    const lemma = useLemma(podId);
    const shape = useShape(podId, name);
    const primaryKey = shape.data?.primary_key_column ?? "id";
    const { rows, count, refresh } = useRows(podId, name, primaryKey, WHOLE);
    const writes = useRowWrites(podId, name, primaryKey);
    const contactsOn = useFeature("contacts");

    const [sharing, setSharing] = useState(false);
    const [search, setSearch] = useState("");
    const [filters, setFilters] = useState<Filters>(NO_FILTERS);
    const [order, setOrder] = useState<Ordering | null>(null);
    const [chosen, setChosen] = useState<string[] | null>(null);
    const [asRows, setAsRows] = useState(false);
    const [sheet, setSheet] = useState<"more" | "columns" | null>(null);
    const [holding, setHolding] = useState<Row | null>(null);
    const [removing, setRemoving] = useState<Row | null>(null);
    const [pulling, setPulling] = useState(false);
    const [askingLemma, setAsking] = useState(false);

    const allRows = rows.data?.pages.flatMap((page) => page.items) ?? [];
    const known = count.isFetched ? count.data : undefined;
    const table = readTable({
        declared: shape.data?.columns,
        rows: allRows,
        primaryKey,
        count: known,
        hasNext: Boolean(rows.hasNextPage),
        asRows,
        chosen,
        filters,
        search,
        order,
    });
    const fixed = useMemo(() => fixedValues(shape.data?.columns), [shape.data]);
    const asked = { ...filters, text: search };
    const asking = isAsking(asked);
    const perUser = shape.data?.enable_rls === true;
    const title = readableName(name);
    const who = lemma?.name ?? "the Lemma";
    /* What the table is shown as, and why, with the way back beside it (the web's filter-bar note). */
    const note = table.shaped ? table.shaped.because : table.choice?.form === "grid" ? table.choice.because : table.choice ? "Table view." : null;
    const loading = rows.isPending || shape.isPending || (!table.settled && !rows.isError);

    const open = (row: Row) => {
        const id = idOf(row, primaryKey);
        if (id) router.push(tablesRoutes.record(podId, name, id) as never);
        else setHolding(row);
    };
    const edit = (row: Row) => {
        const id = idOf(row, primaryKey);
        if (id) router.push(tablesRoutes.edit(podId, name, { id }) as never);
    };
    const add = (preset?: Row) => router.push(tablesRoutes.edit(podId, name, { preset }) as never);

    const more: Action[] = [
        { label: "New row", onPress: () => add() },
        ...(contactsOn ? [{ label: "Collect responses", onPress: () => router.push(tablesRoutes.collect(podId, name) as never) }] : []),
        ...(table.choice && table.choice.form !== "grid" ? [{ label: asRows ? `Back to the ${FORM_NAMES[table.choice.form]}` : "Show as rows", onPress: () => setAsRows(!asRows) }] : []),
        ...(!table.shaped && table.settled && allRows.length ? [{ label: "Columns shown", onPress: () => setSheet("columns") }] : []),
    ];
    const heldId = holding ? idOf(holding, primaryKey) : null;
    const rowActions: Action[] = holding ? [
        ...(heldId ? [{ label: "Open", onPress: () => open(holding) }, { label: "Edit", onPress: () => edit(holding) }] : []),
        { label: "Delete", tone: "danger" as const, onPress: () => setRemoving(holding) },
    ] : [];

    return (
        <FixedValues.Provider value={fixed}>
            <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
                <View style={{ flexDirection: "row", alignItems: "center", paddingTop: 4, paddingLeft: 6, paddingRight: 8 }}>
                    <BackLink label={params.back ? String(params.back) : lemma?.name ?? "Tables"} onPress={() => (router.canGoBack() ? router.back() : router.replace(tablesRoutes.tables(podId) as never))} />
                    <View style={{ flex: 1 }} />
                    <IconButton name="plus" size={22} label="New row" onPress={() => add()} />
                    <Press label={"Share " + title} onPress={() => setSharing(true)} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                        <FilesIcon name="share" size={20} tone={color.ink} />
                    </Press>
                    <IconButton name="more" label="New row, how it is shown, collect responses" onPress={() => setSheet("more")} />
                </View>
                <ShareSheet podId={podId} subject={{ kind: "table", name, label: title }} open={sharing} onClose={() => setSharing(false)} />

                <ScrollView
                    keyboardShouldPersistTaps="handled"
                    contentContainerStyle={{ paddingBottom: 28 }}
                    refreshControl={<RefreshControl refreshing={pulling} onRefresh={() => { setPulling(true); void Promise.all([refresh(), shape.refetch()]).finally(() => setPulling(false)); }} tintColor={color.ink3} />}
                >
                    <View style={{ paddingHorizontal: 20, paddingTop: 4, gap: 4 }}>
                        <Text accessibilityRole="header" size={26} weight="medium" style={{ letterSpacing: -0.4 }}>{title}</Text>
                        {perUser ? <Text size={13.5} tone={color.ink2}>Each person sees only their own rows.</Text> : null}
                        {note ? (
                            <View style={{ flexDirection: "row", alignItems: "center", flexWrap: "wrap", columnGap: 6 }}>
                                <Text size={13.5} tone={color.ink3}>{note}</Text>
                                {table.choice && table.choice.form !== "grid" ? (
                                    <Quiet text={asRows ? `Back to the ${FORM_NAMES[table.choice.form]}` : "Show as rows"} onPress={() => setAsRows(!asRows)} tone={color.ink} />
                                ) : null}
                            </View>
                        ) : null}
                    </View>

                    <View style={{ paddingHorizontal: 16, paddingTop: 10, gap: 10 }}>
                        {allRows.length > 0 || search ? <SearchBox value={search} onChange={setSearch} placeholder={allRows.length > 1 ? `Search ${allRows.length} rows` : "Search"} /> : null}
                        {table.profile && table.settled ? <AskChips profile={table.profile} filters={filters} onFilters={setFilters} order={order} onOrder={setOrder} /> : null}
                        {writes.problem ? <Problem text={writes.problem} onDismiss={writes.clearProblem} /> : null}
                    </View>

                    <View style={{ paddingHorizontal: 16, paddingTop: 4 }}>
                        {rows.isError || shape.isError ? (
                            <Failed error={rows.error ?? shape.error ?? new Error("Some of this table did not load.")} onRetry={() => { void refresh(); void shape.refetch(); }} />
                        ) : loading ? (
                            <Loading label="Reading the table" />
                        ) : allRows.length === 0 ? (
                            <Empty title="No rows yet">
                                <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>Add the first one yourself, or ask {who} to fill it in.</Text>
                                <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, paddingTop: 4 }}>
                                    <Button title="New row" onPress={() => add()} />
                                    <Button title={`Ask ${who} to fill it in`} kind="outline" onPress={() => setAsking(true)} />
                                </View>
                            </Empty>
                        ) : table.matches.length === 0 ? (
                            <View style={{ paddingVertical: 24, paddingHorizontal: 4, gap: 4 }}>
                                <Text size={16}>Nothing here matches what you asked for.</Text>
                                <Quiet text="Clear the search and filters" onPress={() => { setSearch(""); setFilters(NO_FILTERS); }} />
                            </View>
                        ) : table.shaped && table.profile ? (
                            <Layout rows={table.matches} profile={table.profile} choice={table.shaped} primaryKey={primaryKey} onOpen={open} onHold={setHolding} onChange={(row, changes) => void writes.change(row, changes)} onAdd={add} />
                        ) : (
                            <PlainRows rows={table.matches} visible={table.visible} roles={table.roles} primaryKey={primaryKey} onOpen={open} onHold={setHolding} />
                        )}

                        {!loading && allRows.length > 0 ? (
                            <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12, paddingTop: 14, paddingHorizontal: 4 }}>
                                <Text size={13} tone={color.ink3}>{countLine(asking, table.matches.length, allRows.length, known)}</Text>
                                {rows.hasNextPage ? <Quiet text={rows.isFetchingNextPage ? "Loading…" : "Load more"} onPress={() => { if (!rows.isFetchingNextPage) void rows.fetchNextPage(); }} tone={color.ink} /> : null}
                            </View>
                        ) : null}
                    </View>
                </ScrollView>

                <ActionSheet open={sheet === "more"} onClose={() => setSheet(null)} title={title} actions={more} />
                <ColumnsSheet open={sheet === "columns"} onClose={() => setSheet(null)} fields={table.fields} visible={table.visible} onChange={setChosen} />
                <ActionSheet open={holding !== null} onClose={() => setHolding(null)} title={holding ? rowLabel(holding, primaryKey) : undefined} actions={rowActions} />
                <AskSheet open={askingLemma} onClose={() => setAsking(false)} name={who} start={`Add rows to the ${title} table from `} onSend={(text) => router.push(routes.chat(podId, NEW_CHAT, text) as never)} />
                <ConfirmSheet
                    open={removing !== null}
                    onClose={() => setRemoving(null)}
                    title="Delete row"
                    say={removing ? `Delete ${rowLabel(removing, primaryKey)}? It is gone for everyone, and this cannot be undone from here.` : ""}
                    yes="Delete"
                    onYes={() => { const row = removing; setRemoving(null); if (row) void writes.remove(row); }}
                />
            </SafeAreaView>
        </FixedValues.Provider>
    );
}
