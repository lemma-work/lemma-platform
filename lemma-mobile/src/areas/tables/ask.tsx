import { useState } from "react";
import { ScrollView, View } from "react-native";
import { Icon } from "../../ui/icons";
import { Button, Press } from "../../ui/press";
import { Sheet } from "../../ui/sheet";
import { Text } from "../../ui/text";
import { color } from "../../ui/tokens";
import { Chip } from "./bits";
import { filterable, isAsking, NO_FILTERS, orderable, type Filters, type Ordering, type Span } from "@web/library/filters";
import type { TableProfile } from "@web/library/profile";

/** Asking a table something (`lemma-frontend/src/library/filter-bar.tsx`),
 *  folded into a row of chips under the search box: Filter, with how many
 *  are on, and Sort, with what it sorts by. Each opens a sheet of what this
 *  table can be asked: a status offers its values, a date offers when, a
 *  tick offers both of its states. A table with nothing to filter offers no
 *  Filter chip. */

const SPANS: { span: Span; label: string }[] = [
    { span: "any", label: "Any time" },
    { span: "overdue", label: "Overdue" },
    { span: "today", label: "Today" },
    { span: "week", label: "Next 7 days" },
    { span: "later", label: "Later" },
];

const said = (name: string) => name.replace(/_/g, " ");

export function AskChips({ profile, filters, onFilters, order, onOrder }: {
    profile: TableProfile;
    filters: Filters;
    onFilters: (next: Filters) => void;
    order: Ordering | null;
    onOrder: (next: Ordering | null) => void;
}) {
    const [sheet, setSheet] = useState<"filter" | "sort" | null>(null);
    const columns = filterable(profile);
    const sortable = orderable(profile);
    const on = Object.values(filters.values).filter((keep) => keep.length > 0).length + Object.values(filters.spans).filter((span) => span !== "any").length;
    if (columns.length === 0 && sortable.length === 0) return null;
    const toggle = (column: string, value: string) => {
        const keep = filters.values[column] ?? [];
        onFilters({ ...filters, values: { ...filters.values, [column]: keep.includes(value) ? keep.filter((one) => one !== value) : [...keep, value] } });
    };
    return (
        <>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} keyboardShouldPersistTaps="handled" style={{ marginHorizontal: -16 }} contentContainerStyle={{ gap: 6, paddingHorizontal: 16 }}>
                {columns.length > 0 ? <Chip text="Filter" count={on} on={on > 0} label={on ? `Filter, ${on} on` : "Filter"} onPress={() => setSheet("filter")} /> : null}
                {sortable.length > 0 ? (
                    <Chip text={order ? `${said(order.column)} ${order.down ? "↓" : "↑"}` : "Sort"} on={Boolean(order)} label={order ? `Sorted by ${said(order.column)}, ${order.down ? "newest or largest first" : "oldest or smallest first"}. Change` : "Sort"} onPress={() => setSheet("sort")} />
                ) : null}
                {isAsking({ ...filters, text: "" }) ? <Chip text="Clear" on={false} label="Clear the filters" onPress={() => onFilters({ ...NO_FILTERS, text: filters.text })} /> : null}
            </ScrollView>

            <Sheet open={sheet === "filter"} onClose={() => setSheet(null)} title="Filter" tall>
                <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 8, gap: 16 }}>
                    {columns.map((column) => (
                        <View key={column.name} style={{ gap: 8 }}>
                            <Text size={13.5} tone={color.ink2}>{said(column.name)}</Text>
                            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
                                {column.role === "date"
                                    ? SPANS.map(({ span, label }) => (
                                        <Chip key={span} text={label} on={(filters.spans[column.name] ?? "any") === span} onPress={() => onFilters({ ...filters, spans: { ...filters.spans, [column.name]: span } })} />
                                    ))
                                    : column.role === "boolean"
                                        ? (["true", "false"] as const).map((value) => (
                                            <Chip key={value} text={value === "true" ? "Done" : "Not done"} on={(filters.values[column.name] ?? []).includes(value)} onPress={() => toggle(column.name, value)} />
                                        ))
                                        : column.values.map((value) => (
                                            <Chip key={value} text={value} on={(filters.values[column.name] ?? []).includes(value)} onPress={() => toggle(column.name, value)} />
                                        ))}
                            </View>
                        </View>
                    ))}
                    <View style={{ flexDirection: "row", gap: 8, paddingTop: 4 }}>
                        <Button title="Close" grow onPress={() => setSheet(null)} />
                        {on > 0 ? <Button title="Clear" kind="outline" grow onPress={() => onFilters({ ...NO_FILTERS, text: filters.text })} /> : null}
                    </View>
                </ScrollView>
            </Sheet>

            <Sheet open={sheet === "sort"} onClose={() => setSheet(null)} title="Sort" tall>
                <ScrollView>
                    <SortRow label="The table’s own order" on={!order} onPress={() => { onOrder(null); setSheet(null); }} />
                    {sortable.map((column) => (
                        <SortRow key={column.name} label={said(column.name)} on={order?.column === column.name} onPress={() => onOrder({ column: column.name, down: order?.column === column.name ? order.down : true })} />
                    ))}
                </ScrollView>
                {order ? (
                    <View style={{ flexDirection: "row", gap: 8, paddingHorizontal: 20, paddingTop: 10 }}>
                        <Chip text="Newest, largest first" on={order.down} onPress={() => onOrder({ ...order, down: true })} />
                        <Chip text="Oldest, smallest first" on={!order.down} onPress={() => onOrder({ ...order, down: false })} />
                    </View>
                ) : null}
            </Sheet>
        </>
    );
}

function SortRow({ label, on, onPress }: { label: string; on: boolean; onPress: () => void }) {
    return (
        <Press role="radio" label={label} accessibilityState={{ selected: on }} onPress={onPress} style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: 50, paddingHorizontal: 20 }}>
            <Text size={16} style={{ flex: 1 }} numberOfLines={1}>{label}</Text>
            {on ? <Icon name="check" size={18} width={2} /> : null}
        </Press>
    );
}

/** The columns plain rows show: every one, ranked, ticked off to show fewer. */
export function ColumnsSheet({ open, onClose, fields, visible, onChange }: {
    open: boolean;
    onClose: () => void;
    fields: string[];
    visible: string[];
    onChange: (next: string[] | null) => void;
}) {
    return (
        <Sheet open={open} onClose={onClose} title={`Columns shown, ${visible.length} of ${fields.length}`} tall>
            <ScrollView>
                {fields.map((name) => {
                    const on = visible.includes(name);
                    return (
                        <Press
                            key={name}
                            label={name}
                            accessibilityRole="checkbox"
                            accessibilityState={{ checked: on }}
                            onPress={() => onChange(on ? visible.filter((one) => one !== name) : fields.filter((one) => one === name || visible.includes(one)))}
                            style={{ flexDirection: "row", alignItems: "center", gap: 14, minHeight: 50, paddingHorizontal: 20 }}
                        >
                            <View style={{ width: 22, height: 22, borderRadius: 7, borderWidth: on ? 0 : 1.5, borderColor: color.ink3, backgroundColor: on ? color.ink : "transparent", alignItems: "center", justifyContent: "center" }}>
                                {on ? <Icon name="check" size={15} width={2.4} tone={color.onInk} /> : null}
                            </View>
                            <Text size={16} style={{ flex: 1 }} numberOfLines={1}>{name}</Text>
                        </Press>
                    );
                })}
            </ScrollView>
            <View style={{ flexDirection: "row", gap: 8, paddingHorizontal: 20, paddingTop: 10 }}>
                <Button title="Done" grow onPress={onClose} />
                <Button title="Show every column" kind="outline" grow onPress={() => onChange(null)} />
            </View>
        </Sheet>
    );
}
