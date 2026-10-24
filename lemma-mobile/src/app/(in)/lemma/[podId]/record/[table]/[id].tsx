import { useMemo, useState } from "react";
import { RefreshControl, ScrollView, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { BackLink, IconButton, Loading, Problem as Failed } from "../../../../../../ui/chrome";
import { Icon } from "../../../../../../ui/icons";
import { Press } from "../../../../../../ui/press";
import { Counted, Num, Text } from "../../../../../../ui/text";
import { color, TOUCH } from "../../../../../../ui/tokens";
import { ActionSheet, ConfirmSheet, FixedValues, GroupHead, Mover, Problem, Quiet } from "../../../../../../areas/tables/bits";
import { fixedValues } from "../../../../../../areas/tables/layouts";
import { useLinkedBack, useLinkedOut, useRecord, useRowSample, useRowWrites, useShape, useShapes } from "../../../../../../areas/tables/hooks";
import { tablesRoutes, withBack } from "../../../../../../areas/tables/routes";
import { layoutRecord, profileTable, type ColumnProfile } from "@web/library/profile";
import { readableName, whenever } from "@web/library/reading";
import { cellText, rowLabel, type Row } from "@web/library/record-cache";
import { inwardLinks, linkLabel, LINKED_ROWS_SHOWN, outwardLinks } from "@web/library/relations";

/** One row, and what it is attached to (`lemma-frontend` `RecordView`).
 *
 *  The fields in the web's four bands: the state at the top as the control
 *  that changes it, the facts as a short list with dates said in words, long
 *  text with the width to itself, and the key and timestamps folded away.
 *  Then the links, read from the schema both ways: what this row points at,
 *  and the rows of other tables that point back at it. Live, like its table. */
export default function RecordScreen() {
    const router = useRouter();
    const params = useLocalSearchParams<{ podId: string; table: string; id: string; back?: string }>();
    const podId = String(params.podId);
    const tableName = String(params.table);
    const recordId = String(params.id);
    const shape = useShape(podId, tableName);
    const primaryKey = shape.data?.primary_key_column ?? "id";
    const record = useRecord(podId, tableName, recordId);
    const shapes = useShapes(podId);
    const sample = useRowSample(podId, tableName);
    const writes = useRowWrites(podId, tableName, primaryKey);
    const [empties, setEmpties] = useState(false);
    const [quiet, setQuiet] = useState(false);
    const [sheet, setSheet] = useState(false);
    const [removing, setRemoving] = useState(false);
    const [pulling, setPulling] = useState(false);

    const row: Row = record.data ?? {};
    const out = useMemo(() => outwardLinks(shape.data), [shape.data]);
    const back = useMemo(() => (shapes.data ? inwardLinks(shapes.data, tableName) : []), [shapes.data, tableName]);
    const pointedAt = useLinkedOut(podId, out, record.data);
    const referencing = useLinkedBack(podId, back, recordId);
    const tableTitle = readableName(tableName);
    const title = rowLabel(row, primaryKey);

    const profile = sample.data && record.data
        ? profileTable(Object.keys(row).map((name) => ({ name })), sample.data.length ? sample.data : [row], { primaryKey })
        : null;
    const layout = profile ? layoutRecord(profile, out.map((link) => link.column)) : null;
    const filled = (column: ColumnProfile) => {
        const value = row[column.name];
        return !(value === null || value === undefined || value === "");
    };
    const said = (column: ColumnProfile): string => {
        const value = row[column.name];
        if (column.role === "boolean") return value === true ? "Yes" : value === false ? "No" : cellText(value);
        if (column.role !== "date") return cellText(value);
        const when = whenever(value);
        return when ? cellText(value).slice(0, 10) + " · " + when.text : cellText(value);
    };
    const goBack = () => (router.canGoBack() ? router.back() : router.replace(tablesRoutes.table(podId, tableName) as never));
    const openTable = (name: string) => router.push(withBack(tablesRoutes.table(podId, name), title) as never);
    const openRecord = (name: string, id: string) => router.push(withBack(tablesRoutes.record(podId, name, id), title) as never);

    const head = (
        <View style={{ flexDirection: "row", alignItems: "center", paddingTop: 4, paddingLeft: 6, paddingRight: 8 }}>
            <BackLink label={params.back ? String(params.back) : tableTitle} onPress={goBack} />
            <View style={{ flex: 1 }} />
            {record.data ? <IconButton name="pencil" size={20} label="Edit this row" onPress={() => router.push(tablesRoutes.edit(podId, tableName, { id: recordId }) as never)} /> : null}
            {record.data ? <IconButton name="more" label="Delete, open the table" onPress={() => setSheet(true)} /> : null}
        </View>
    );

    if (record.isPending || shape.isPending) {
        return <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>{head}<Loading label="Reading the row" /></SafeAreaView>;
    }
    if (record.isError) {
        return (
            <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
                {head}
                <Failed error={new Error("That row could not be read. It may have been deleted.")} onRetry={() => void record.refetch()} />
            </SafeAreaView>
        );
    }

    const facts = layout ? layout.facts.filter(filled) : [];
    const blank = layout ? [...layout.facts, ...layout.prose].filter((column) => !filled(column)) : [];

    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            {head}
            <ScrollView
                contentContainerStyle={{ paddingBottom: 32 }}
                refreshControl={<RefreshControl refreshing={pulling} onRefresh={() => { setPulling(true); void Promise.all([record.refetch(), shapes.refetch(), ...referencing.map((one) => one.refetch())]).finally(() => setPulling(false)); }} tintColor={color.ink3} />}
            >
                <View style={{ paddingHorizontal: 20, paddingTop: 4, gap: 8 }}>
                    <Counted size={24} weight="medium" style={{ letterSpacing: -0.24 }}>{title}</Counted>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
                        <Press role="link" label={`Open ${tableTitle}`} onPress={() => openTable(tableName)} hitSlop={8} style={{ flexDirection: "row", alignItems: "center", gap: 5 }}>
                            <Icon name="table" size={14} tone={color.ink2} />
                            <Text size={13.5} tone={color.ink2}>{tableTitle}</Text>
                        </Press>
                        <Num size={12.5} tone={color.ink3} numberOfLines={1} style={{ flex: 1 }}>{recordId}</Num>
                    </View>
                    {layout?.state && filled(layout.state) ? (
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 10, paddingTop: 4 }}>
                            <FixedValues.Provider value={fixedValues(shape.data?.columns)}>
                                <Mover column={layout.state} row={row} onChange={(at, changes) => void writes.change(at, changes)} wide />
                            </FixedValues.Provider>
                            <Text size={13} tone={color.ink3}>{layout.state.name.replace(/_/g, " ")}</Text>
                        </View>
                    ) : null}
                    {writes.problem ? <Problem text={writes.problem} onDismiss={writes.clearProblem} /> : null}
                </View>

                <View style={{ paddingHorizontal: 16 }}>
                    {layout ? (
                        <>
                            <GroupHead text="Fields" />
                            {facts.map((column) => <Fact key={column.name} name={column.name} value={said(column)} />)}
                            {blank.length > 0 ? <Quiet text={`${empties ? "Hide" : "Show"} ${blank.length} empty ${blank.length === 1 ? "field" : "fields"}`} onPress={() => setEmpties(!empties)} /> : null}
                            {empties ? blank.map((column) => <Fact key={column.name} name={column.name} value="—" faint />) : null}
                            {layout.prose.filter(filled).map((column) => (
                                <View key={column.name}>
                                    <GroupHead text={column.name.replace(/_/g, " ")} />
                                    <Text size={15.5} style={{ lineHeight: 23, paddingHorizontal: 4 }} selectable>{String(row[column.name] ?? "")}</Text>
                                </View>
                            ))}
                        </>
                    ) : (
                        /* Before the sample lands there is nothing to rank by: the
                           row in the order it arrived. */
                        <>
                            <GroupHead text="Fields" />
                            {Object.entries(row).map(([name, value]) => <Fact key={name} name={name} value={cellText(value)} />)}
                        </>
                    )}

                    {out.length > 0 ? (
                        <>
                            <GroupHead text="Linked records" />
                            {out.map((link, index) => {
                                const value = row[link.column];
                                const id = value === null || value === undefined ? "" : String(value);
                                const target = pointedAt[index];
                                if (!id) return <Fact key={link.column} name={link.column} value="not set" faint />;
                                if (target?.isError) return <Fact key={link.column} name={link.column} value={`${readableName(link.table)} · not readable`} faint />;
                                return (
                                    <LinkLine
                                        key={link.column}
                                        why={link.column}
                                        label={target?.isPending ? "…" : linkLabel(target?.data, id, (other) => rowLabel(other, "id"))}
                                        where={readableName(link.table)}
                                        onPress={() => openRecord(link.table, id)}
                                    />
                                );
                            })}
                        </>
                    ) : null}

                    {back.length > 0 ? <GroupHead text="Referenced by" /> : null}
                    {back.map((link, index) => {
                        const found = referencing[index];
                        const rows = found?.data ?? [];
                        const more = rows.length > LINKED_ROWS_SHOWN;
                        return (
                            <View key={link.table + "." + link.column}>
                                <Press role="link" label={`Open ${readableName(link.table)}`} onPress={() => openTable(link.table)} style={{ flexDirection: "row", alignItems: "center", gap: 6, minHeight: 36, paddingHorizontal: 4, marginTop: index === 0 ? 0 : 10 }}>
                                    <Icon name="table" size={14} tone={color.ink2} />
                                    <Text size={14} tone={color.ink2}>{readableName(link.table)}</Text>
                                    <Text size={13} tone={color.ink3}>via {link.column}</Text>
                                </Press>
                                {found?.isPending ? <Loading label="Reading linked rows" />
                                    : found?.isError ? <Text size={14} tone={color.ink3} style={{ paddingHorizontal: 4, paddingVertical: 8 }}>Couldn’t read the linked rows.</Text>
                                    : rows.length === 0 ? <Text size={14} tone={color.ink3} style={{ paddingHorizontal: 4, paddingVertical: 8 }}>Nothing yet.</Text>
                                    : rows.slice(0, LINKED_ROWS_SHOWN).map((other, at) => {
                                        const otherId = String(other.id ?? at);
                                        return <LinkLine key={otherId} label={rowLabel(other, "id")} onPress={() => openRecord(link.table, otherId)} />;
                                    })}
                                {more ? <Quiet text={`More in ${readableName(link.table)}`} onPress={() => openTable(link.table)} /> : null}
                            </View>
                        );
                    })}

                    {layout && layout.quiet.length > 0 ? (
                        <>
                            <View style={{ height: 10 }} />
                            <Quiet text={quiet ? "Hide identifiers and timestamps" : "Identifiers and timestamps"} onPress={() => setQuiet(!quiet)} />
                            {quiet ? layout.quiet.map((column) => <Fact key={column.name} name={column.name} value={said(column)} faint />) : null}
                        </>
                    ) : null}
                </View>
            </ScrollView>

            <ActionSheet
                open={sheet}
                onClose={() => setSheet(false)}
                title={title}
                actions={[
                    { label: "Edit", onPress: () => router.push(tablesRoutes.edit(podId, tableName, { id: recordId }) as never) },
                    { label: `Open ${tableTitle}`, onPress: () => openTable(tableName) },
                    { label: "Delete row", tone: "danger", onPress: () => setRemoving(true) },
                ]}
            />
            <ConfirmSheet
                open={removing}
                onClose={() => setRemoving(false)}
                title="Delete row"
                say={`Delete ${title}? It is gone for everyone, and this cannot be undone from here.`}
                yes="Delete"
                onYes={() => { setRemoving(false); void writes.remove(row).then((gone) => { if (gone) goBack(); }); }}
            />
        </SafeAreaView>
    );
}

/** A field: its name, then its value, wrapping rather than cut. */
function Fact({ name, value, faint = false }: { name: string; value: string; faint?: boolean }) {
    return (
        <View style={{ flexDirection: "row", gap: 12, paddingVertical: 9, paddingHorizontal: 4, borderBottomWidth: 1, borderBottomColor: color.line }}>
            <Text size={13.5} tone={color.ink3} style={{ width: "36%", paddingTop: 1 }}>{name.replace(/_/g, " ")}</Text>
            <Counted size={15} tone={faint ? color.ink3 : color.ink} style={{ flex: 1, lineHeight: 21 }}>{value}</Counted>
        </View>
    );
}

/** A row on the other end of a link: what it is called, and its table. */
function LinkLine({ why, label, where, onPress }: { why?: string; label: string; where?: string; onPress: () => void }) {
    return (
        <Press role="link" label={`${label}${where ? `, in ${where}` : ""}`} onPress={onPress} style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: TOUCH + 4, paddingHorizontal: 4, borderBottomWidth: 1, borderBottomColor: color.line }}>
            {why ? <Text size={13.5} tone={color.ink3} numberOfLines={1} style={{ width: "36%" }}>{why.replace(/_/g, " ")}</Text> : null}
            <View style={{ flex: 1, minWidth: 0 }}>
                <Counted size={15.5} numberOfLines={1}>{label}</Counted>
                {where ? <Text size={12.5} tone={color.ink3}>{where}</Text> : null}
            </View>
            <Icon name="forward" size={16} tone={color.ink3} />
        </Press>
    );
}
