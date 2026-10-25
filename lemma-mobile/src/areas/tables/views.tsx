import { useRef, useState, type ReactNode } from "react";
import { PanResponder, ScrollView, View, type GestureResponderEvent, type LayoutChangeEvent } from "react-native";
import Svg, { Circle, Line, Path } from "react-native-svg";
import { IconButton } from "../../ui/chrome";
import { Press } from "../../ui/press";
import { Counted, Num, Text } from "../../ui/text";
import { color, TOUCH } from "../../ui/tokens";
import { GroupHead, Mover, Quiet, Tag, Tick } from "./bits";
import { besides, cellFor, checklistOrder, DRAWN, PAGEFUL, PILE, pileOrder, series, supporting, timelineDays, valueText } from "./layouts";
import { isFinished, type ColumnProfile, type FormChoice, type TableProfile } from "@web/library/profile";
import { compact, dayStart, whenever } from "@web/library/reading";
import { cellText, idOf, rowLabel, type Row } from "@web/library/record-cache";

/** A table in the shape the web chose for it, drawn for a phone
 *  (`lemma-frontend/src/library/forms.tsx`). Every shape draws the same rows;
 *  what changes is which fact gets the space. On a phone that means one dense
 *  line a row wherever a row fits on one, rules rather than boxes, and the
 *  two edits a layout is for — ticking an item, moving a card — done where
 *  the row is. Holding a row offers the rest (open, edit, delete). */

export interface LayoutProps {
    rows: Row[];
    profile: TableProfile;
    choice: FormChoice;
    primaryKey: string;
    onOpen: (row: Row) => void;
    /** Hold a row, or its "more": open, edit, delete. */
    onHold: (row: Row) => void;
    /** One field, where it is. */
    onChange: (row: Row, changes: Row) => void;
    /** A new row, with what the place it was added from implies. */
    onAdd: (preset?: Row) => void;
}

export function Layout(props: LayoutProps) {
    switch (props.choice.form) {
        case "board": return <Board {...props} />;
        case "checklist": return <Checklist {...props} />;
        case "timeline": return <Timeline {...props} />;
        case "cards": return <Cards {...props} />;
        case "feed": return <Feed {...props} />;
        case "chart": return <Chart {...props} />;
        case "grid": return null;
    }
}

function useCapped<T>(items: T[], size = PAGEFUL) {
    const [cap, setCap] = useState(size);
    return { shown: items.length > cap ? items.slice(0, cap) : items, hidden: Math.max(0, items.length - cap), more: () => setCap((was) => was + size) };
}

function MoreRows({ hidden, onMore }: { hidden: number; onMore: () => void }) {
    if (hidden <= 0) return null;
    return <Quiet text={`Show more (${hidden} left)`} onPress={onMore} />;
}

/** One row on one line: the heading, then whatever the layout puts at its end. */
function Line1({ row, heading, onOpen, onHold, start, end, done = false }: {
    row: Row;
    heading: string;
    onOpen: (row: Row) => void;
    onHold: (row: Row) => void;
    start?: ReactNode;
    end?: ReactNode;
    done?: boolean;
}) {
    return (
        <View style={{ flexDirection: "row", alignItems: "center", minHeight: 48, borderBottomWidth: 1, borderBottomColor: color.line }}>
            {start}
            <Press role="link" label={heading} onPress={() => onOpen(row)} onLongPress={() => onHold(row)} style={{ flex: 1, minWidth: 0, minHeight: 48, justifyContent: "center", paddingLeft: start ? 2 : 4, paddingRight: 8 }}>
                <Counted size={15.5} numberOfLines={1} tone={done ? color.ink3 : color.ink} style={done ? { textDecorationLine: "line-through" } : undefined}>{heading}</Counted>
            </Press>
            {end ? <View style={{ flexDirection: "row", alignItems: "center", gap: 8, flexShrink: 1, maxWidth: "52%", paddingRight: 4 }}>{end}</View> : null}
        </View>
    );
}

function Beside({ row, column }: { row: Row; column: ColumnProfile }) {
    if (column.role === "enum") return <Tag text={cellText(row[column.name])} />;
    return <Counted size={13} tone={color.ink3} numberOfLines={1} style={{ flexShrink: 1 }}>{valueText(row, column)}</Counted>;
}

/* ---- board ------------------------------------------------------------- */

/** Piles, one under the other: a phone has no room for them side by side,
 *  and scrolling sideways to find a column hides every other one. Each card
 *  carries its mover; "Add" under a pile starts the row in it. */
function Board({ rows, profile, choice, primaryKey, onOpen, onHold, onChange, onAdd }: LayoutProps) {
    const by = choice.around ?? "";
    const axis = profile.columns.find((column) => column.name === by);
    const beside = supporting(profile, by, Infinity).filter((column) => column.role !== "name").slice(0, 1);
    const held = useRef<string[]>([]);
    held.current = pileOrder(held.current, rows.map((row) => cellText(row[by])));
    return (
        <View>
            {held.current.map((pile) => (
                <Pile key={pile} pile={pile} rows={rows.filter((row) => cellText(row[by]) === pile)} by={by} axis={axis} beside={beside} primaryKey={primaryKey} onOpen={onOpen} onHold={onHold} onChange={onChange} onAdd={onAdd} />
            ))}
        </View>
    );
}

function Pile({ pile, rows, by, axis, beside, primaryKey, onOpen, onHold, onChange, onAdd }: {
    pile: string;
    rows: Row[];
    by: string;
    axis?: ColumnProfile;
    beside: ColumnProfile[];
    primaryKey: string;
    onOpen: (row: Row) => void;
    onHold: (row: Row) => void;
    onChange: (row: Row, changes: Row) => void;
    onAdd: (preset?: Row) => void;
}) {
    const capped = useCapped(rows, PILE);
    return (
        <View>
            <GroupHead text={pile} count={rows.length} right={<Quiet text="Add" label={`Add to ${pile}`} onPress={() => onAdd({ [by]: pile })} />} />
            {rows.length === 0 ? <Text size={14} tone={color.ink3} style={{ paddingHorizontal: 4, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: color.line }}>Nothing here now.</Text> : null}
            {capped.shown.map((row, index) => {
                const heading = rowLabel(row, primaryKey);
                return (
                    <Line1 key={idOf(row, primaryKey) ?? index} row={row} heading={heading} onOpen={onOpen} onHold={onHold} end={<>
                        {besides(row, beside, heading).map((column) => <Beside key={column.name} row={row} column={column} />)}
                        {axis ? <Mover column={axis} row={row} onChange={onChange} /> : null}
                    </>} />
                );
            })}
            <MoreRows hidden={capped.hidden} onMore={capped.more} />
        </View>
    );
}

/* ---- checklist --------------------------------------------------------- */

function Checklist({ rows, profile, choice, primaryKey, onOpen, onHold, onChange, onAdd }: LayoutProps) {
    const when = choice.around ?? "";
    const state = profile.columns.find((column) => column.role === "boolean") ?? profile.columns.find((column) => column.role === "enum" && column.distinct <= 4);
    const beside = supporting(profile, when, 4).filter((column) => column.name !== state?.name && column.role !== "name").slice(0, 1);
    const settled = (row: Row) => (state ? isFinished(row[state.name]) : false);
    const { rows: sorted, left } = checklistOrder(rows, when, settled);
    const tickable = state?.role === "boolean";
    const capped = useCapped(sorted);
    return (
        <View>
            <GroupHead text={`${left} left of ${rows.length}`} right={<Quiet text="Add" label="Add an item" onPress={() => onAdd()} />} />
            {capped.shown.map((row, index) => {
                const due = whenever(row[when]);
                const done = settled(row);
                const heading = rowLabel(row, primaryKey);
                const extra = besides(row, beside, heading)[0];
                return (
                    <Line1
                        key={idOf(row, primaryKey) ?? index}
                        row={row}
                        heading={heading}
                        done={done}
                        onOpen={onOpen}
                        onHold={onHold}
                        start={tickable && state ? <Tick done={done} label={heading} onPress={() => onChange(row, { [state.name]: !done })} /> : <Tick done={done} label={heading} />}
                        end={<>
                            {extra ? <Text size={13} tone={color.ink3} numberOfLines={1} style={{ flexShrink: 1 }}>{valueText(row, extra)}</Text> : null}
                            {due ? <Num size={13} tone={!done && due.days < 0 ? color.amber : color.ink3} numberOfLines={1}>{due.text}</Num> : null}
                            {!tickable && state ? <Mover column={state} row={row} onChange={onChange} /> : null}
                        </>}
                    />
                );
            })}
            <MoreRows hidden={capped.hidden} onMore={capped.more} />
        </View>
    );
}

/* ---- timeline ---------------------------------------------------------- */

function dayName(day: string): string {
    const at = dayStart(day);
    if (!Number.isFinite(at)) return day || "No date";
    return new Date(at).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

/** Newest first, the day said once over the things that happened on it. */
function Timeline({ rows, profile, choice, primaryKey, onOpen, onHold }: LayoutProps) {
    const when = choice.around ?? "";
    const beside = supporting(profile, when, 3).slice(0, 2);
    const capped = useCapped(rows);
    const days = timelineDays(capped.shown, when);
    return (
        <View>
            {days.map((day) => (
                <View key={day.day}>
                    <GroupHead text={day.said ? `${dayName(day.day)} · ${day.said}` : dayName(day.day)} />
                    {day.rows.map((row, index) => {
                        const heading = rowLabel(row, primaryKey);
                        return (
                            <Line1 key={idOf(row, primaryKey) ?? index} row={row} heading={heading} onOpen={onOpen} onHold={onHold} end={besides(row, beside, heading).map((column) => <Beside key={column.name} row={row} column={column} />)} />
                        );
                    })}
                </View>
            ))}
            <MoreRows hidden={capped.hidden} onMore={capped.more} />
        </View>
    );
}

/* ---- cards ------------------------------------------------------------- */

/** One block a row: its name, then its few best facts as label and value,
 *  a status shown as the control that changes it. */
function Cards({ rows, profile, choice, primaryKey, onOpen, onHold, onChange, onAdd }: LayoutProps) {
    const named = choice.around;
    const beside = supporting(profile, named, named ? 4 : 6);
    const capped = useCapped(rows);
    return (
        <View>
            <GroupHead text={rows.length === 1 ? "1 card" : `${rows.length} cards`} right={<Quiet text="Add" label="Add a card" onPress={() => onAdd()} />} />
            {capped.shown.map((row, index) => {
                const heading = rowLabel(row, primaryKey);
                return (
                    <View key={idOf(row, primaryKey) ?? index} style={{ paddingTop: 10, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: color.line }}>
                        <View style={{ flexDirection: "row", alignItems: "center" }}>
                            <Press role="link" label={heading} onPress={() => onOpen(row)} onLongPress={() => onHold(row)} style={{ flex: 1, minHeight: 36, justifyContent: "center", paddingHorizontal: 4 }}>
                                <Counted size={16.5} weight="medium" numberOfLines={2}>{heading}</Counted>
                            </Press>
                            <IconButton name="more" label={`More for ${heading}`} onPress={() => onHold(row)} size={18} />
                        </View>
                        {besides(row, beside, heading).map((column) => (
                            <View key={column.name} style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: 30, paddingHorizontal: 4 }}>
                                <Text size={13} tone={color.ink3} numberOfLines={1} style={{ width: "36%" }}>{column.name.replace(/_/g, " ")}</Text>
                                <View style={{ flex: 1, flexDirection: "row" }}>
                                    {column.role === "enum" ? <Mover column={column} row={row} onChange={onChange} /> : <Counted size={14.5} numberOfLines={2}>{valueText(row, column)}</Counted>}
                                </View>
                            </View>
                        ))}
                    </View>
                );
            })}
            <MoreRows hidden={capped.hidden} onMore={capped.more} />
        </View>
    );
}

/* ---- feed -------------------------------------------------------------- */

/** The long text is why the row exists, so it gets the width: a few lines of
 *  it, and the rest a tap away. */
function Feed({ rows, profile, choice, primaryKey, onOpen, onHold }: LayoutProps) {
    const prose = choice.around ?? "";
    const beside = supporting(profile, prose, 3);
    const capped = useCapped(rows);
    return (
        <View>
            {capped.shown.map((row, index) => {
                const heading = rowLabel(row, primaryKey);
                const tags = besides(row, beside, heading);
                return (
                    <View key={idOf(row, primaryKey) ?? index} style={{ paddingTop: 12, paddingBottom: 14, borderBottomWidth: 1, borderBottomColor: color.line, gap: 6 }}>
                        <View style={{ flexDirection: "row", alignItems: "center" }}>
                            <Press role="link" label={heading} onPress={() => onOpen(row)} onLongPress={() => onHold(row)} style={{ flex: 1, minHeight: 32, justifyContent: "center", paddingHorizontal: 4 }}>
                                <Counted size={16.5} weight="medium" numberOfLines={2}>{heading}</Counted>
                            </Press>
                            <IconButton name="more" label={`More for ${heading}`} onPress={() => onHold(row)} size={18} />
                        </View>
                        {tags.length ? (
                            <View style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 8, paddingHorizontal: 4 }}>
                                {tags.map((column) => <Beside key={column.name} row={row} column={column} />)}
                            </View>
                        ) : null}
                        <Prose text={String(row[prose] ?? "")} />
                    </View>
                );
            })}
            <MoreRows hidden={capped.hidden} onMore={capped.more} />
        </View>
    );
}

const PROSE_LINES = 5;

/** Clipped to a few lines, with "More" only where there is more: measured
 *  from a copy laid out in full, never guessed from its length. */
function Prose({ text }: { text: string }) {
    const [open, setOpen] = useState(false);
    const [lines, setLines] = useState(0);
    const clipped = lines > PROSE_LINES;
    return (
        <View style={{ paddingHorizontal: 4 }}>
            <Text size={15} tone={color.ink2} numberOfLines={open ? undefined : PROSE_LINES} style={{ lineHeight: 21.5 }}>{text}</Text>
            <View pointerEvents="none" accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={{ position: "absolute", left: 4, right: 4, opacity: 0 }}>
                <Text size={15} style={{ lineHeight: 21.5 }} onTextLayout={(event) => setLines(event.nativeEvent.lines.length)}>{text}</Text>
            </View>
            {clipped ? <Quiet text={open ? "Less" : "More"} onPress={() => setOpen(!open)} /> : null}
        </View>
    );
}

/* ---- chart ------------------------------------------------------------- */

/** One measure a plot, never two scales on one frame; a thumb along it reads
 *  each point. */
function Chart({ rows, profile, choice }: LayoutProps) {
    const when = choice.around ?? "";
    const measures = profile.columns.filter((column) => column.role === "number");
    return (
        <View style={{ gap: 18, paddingTop: 12 }}>
            {measures.map((column) => <Plot key={column.name} label={column.name.replace(/_/g, " ")} points={series(rows, when, column.name)} />)}
        </View>
    );
}

const PLOT_H = 150;
const PAD = { top: 12, right: 8, bottom: 8, left: 40 };

function Plot({ label, points }: { label: string; points: { at: number; value: number; label: string }[] }) {
    const [width, setWidth] = useState(0);
    const [held, setHeld] = useState<number | null>(null);
    if (points.length < 2) return null;
    const values = points.map((point) => point.value);
    const low = Math.min(...values);
    const high = Math.max(...values);
    const span = high - low || Math.abs(high) || 1;
    const top = high + span * 0.12;
    const bottom = Math.min(low - span * 0.12, low >= 0 ? 0 : low - span * 0.12);
    const x = (index: number) => PAD.left + (index / (points.length - 1)) * Math.max(1, width - PAD.left - PAD.right);
    const y = (value: number) => PAD.top + (1 - (value - bottom) / (top - bottom || 1)) * (PLOT_H - PAD.top - PAD.bottom);
    const line = points.map((point, index) => (index ? "L" : "M") + x(index).toFixed(1) + " " + y(point.value).toFixed(1)).join(" ");
    const ticks = [bottom, (bottom + top) / 2, top];
    const peak = points.reduce((best, point, index) => (point.value > points[best].value ? index : best), 0);
    const shown = held ?? points.length - 1;
    /* A thumb along the plot reads it; a thumb up or down the plot is
       scrolling the page, so the page may take it back. */
    const scrub = PanResponder.create({
        onStartShouldSetPanResponder: () => true,
        onMoveShouldSetPanResponder: (_, gesture) => Math.abs(gesture.dx) > Math.abs(gesture.dy),
        onPanResponderTerminationRequest: (_, gesture) => Math.abs(gesture.dy) > Math.abs(gesture.dx),
        onPanResponderGrant: (event) => touch(event),
        onPanResponderMove: (event) => touch(event),
    });
    const touch = (event: GestureResponderEvent) => {
        const at = event.nativeEvent.locationX;
        const index = Math.round(((at - PAD.left) / Math.max(1, width - PAD.left - PAD.right)) * (points.length - 1));
        setHeld(Math.max(0, Math.min(points.length - 1, index)));
    };
    return (
        <View style={{ gap: 6 }}>
            <View style={{ flexDirection: "row", alignItems: "baseline", gap: 8, paddingHorizontal: 4 }}>
                <Text size={15} weight="medium">{label}</Text>
                <Num size={13} tone={color.ink3}>{compact(points[points.length - 1].value)} latest · {compact(points[peak].value)} peak</Num>
            </View>
            <View
                accessible
                accessibilityRole="image"
                accessibilityLabel={`${label} over time, ${points.length} points, peak ${compact(points[peak].value)}, latest ${compact(points[points.length - 1].value)}`}
                onLayout={(event: LayoutChangeEvent) => setWidth(event.nativeEvent.layout.width)}
                {...scrub.panHandlers}
                style={{ height: PLOT_H }}
            >
                {width > 0 ? (
                    <Svg width={width} height={PLOT_H}>
                        {ticks.map((tick, index) => (
                            <Line key={index} x1={PAD.left} x2={width - PAD.right} y1={y(tick)} y2={y(tick)} stroke={color.line2} strokeWidth={1} />
                        ))}
                        <Path d={line} stroke={color.ink} strokeWidth={2} fill="none" strokeLinejoin="round" strokeLinecap="round" />
                        <Line x1={x(shown)} x2={x(shown)} y1={PAD.top} y2={PLOT_H - PAD.bottom} stroke={color.line2} strokeWidth={1} />
                        <Circle cx={x(shown)} cy={y(points[shown].value)} r={4.5} fill={color.ink} />
                    </Svg>
                ) : null}
                {ticks.map((tick, index) => (
                    <Num key={index} size={11} tone={color.ink3} style={{ position: "absolute", left: 0, width: PAD.left - 6, textAlign: "right", top: y(tick) - 7 }}>{compact(tick)}</Num>
                ))}
            </View>
            <View style={{ flexDirection: "row", justifyContent: "space-between", paddingLeft: PAD.left, paddingRight: PAD.right }}>
                <Num size={11} tone={color.ink3}>{points[0].label}</Num>
                <Num size={11} tone={color.ink3}>{points[points.length - 1].label}</Num>
            </View>
            <Num accessibilityLiveRegion="polite" size={13.5} tone={color.ink2} style={{ paddingHorizontal: 4 }}>{points[shown].label} · {compact(points[shown].value)}</Num>
        </View>
    );
}

/* ---- plain rows -------------------------------------------------------- */

const WIDTH: Record<string, number> = { name: 200, prose: 240, text: 170, enum: 116, date: 120, number: 96, boolean: 76, link: 150, key: 150, bookkeeping: 150, structured: 180, empty: 100 };

/** Every column shown, a row a line, scrolled sideways to see more of it.
 *  The columns are ranked rather than cut; "Columns" picks fewer. */
export function PlainRows({ rows, visible, roles, primaryKey, onOpen, onHold }: {
    rows: Row[];
    visible: string[];
    roles: Map<string, string>;
    primaryKey: string;
    onOpen: (row: Row) => void;
    onHold: (row: Row) => void;
}) {
    const [drawn, setDrawn] = useState(DRAWN);
    const shown = rows.slice(0, drawn);
    const width = (name: string) => WIDTH[roles.get(name) ?? "text"] ?? 150;
    return (
        <View>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginHorizontal: -16 }} contentContainerStyle={{ paddingHorizontal: 16 }}>
                <View>
                    <View style={{ flexDirection: "row", minHeight: 36, alignItems: "center", borderBottomWidth: 1, borderBottomColor: color.line2 }}>
                        {visible.map((name) => (
                            <Text key={name} size={12.5} tone={color.ink3} numberOfLines={1} style={{ width: width(name), paddingHorizontal: 4, textAlign: roles.get(name) === "number" ? "right" : "left" }}>{name}</Text>
                        ))}
                    </View>
                    {shown.map((row, index) => (
                        <Press key={idOf(row, primaryKey) ?? index} role="link" label={rowLabel(row, primaryKey)} onPress={() => onOpen(row)} onLongPress={() => onHold(row)} style={{ flexDirection: "row", alignItems: "center", minHeight: TOUCH + 2, borderBottomWidth: 1, borderBottomColor: color.line }}>
                            {visible.map((name) => {
                                const role = roles.get(name);
                                const value = row[name];
                                return (
                                    <View key={name} style={{ width: width(name), paddingHorizontal: 4, alignItems: role === "number" ? "flex-end" : "flex-start" }}>
                                        {role === "enum" && value !== null && value !== undefined && value !== "" ? <Tag text={cellText(value)} />
                                            : role === "number" ? <Num size={14.5} tone={color.ink} numberOfLines={1}>{cellFor(value, role)}</Num>
                                            : <Text size={14.5} numberOfLines={1} tone={value === null || value === undefined ? color.ink3 : color.ink}>{cellFor(value, role)}</Text>}
                                    </View>
                                );
                            })}
                        </Press>
                    ))}
                </View>
            </ScrollView>
            <MoreRows hidden={rows.length - shown.length} onMore={() => setDrawn((was) => was + DRAWN)} />
        </View>
    );
}
