import { useState, type ReactNode } from "react";
import { ScrollView, View } from "react-native";
import { Glyph } from "../../thread/glyphs";
import { LemmaFace } from "../../ui/face";
import { Icon } from "../../ui/icons";
import { Press } from "../../ui/press";
import { Num, Text } from "../../ui/text";
import { color, font, TOUCH } from "../../ui/tokens";
import { fieldsOf, type RunMark, type Shown } from "./model";
import type { NodeState } from "@web/workflow/run-tree";

/** The pieces the work screens are drawn with. Rules, not boxes: one line a
 *  row on a hairline; violet only for what needs the reader or is moving,
 *  amber for what went wrong. */

/** How a run's row ends: a violet dot when it waits on you, a violet ring
 *  while it moves, an amber dot when it failed, nothing when it is done. */
export function RunEnd({ mark }: { mark: RunMark }) {
    if (mark === "yours") return <View accessibilityLabel="Waiting on you" style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: color.violet }} />;
    if (mark === "failed") return <View accessibilityLabel="Failed" style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: color.amber }} />;
    if (mark === "going") {
        return (
            <View accessibilityLabel="Running" style={{ width: 13, height: 13, borderRadius: 7, backgroundColor: color.violetWash, alignItems: "center", justifyContent: "center" }}>
                <View style={{ width: 9, height: 9, borderRadius: 5, borderWidth: 2, borderColor: color.violet }} />
            </View>
        );
    }
    return null;
}

/** One line on a hairline: a glyph, what it is (with a quiet word after it),
 *  and how it ends. Pressing it opens it. */
export function Line({ glyph, title, quiet, tone, end, when, label, onPress, onLongPress }: {
    glyph?: ReactNode;
    title: string;
    quiet?: string | null;
    tone?: string;
    end?: ReactNode;
    when?: string | null;
    label?: string;
    onPress?: () => void;
    onLongPress?: () => void;
}) {
    const body = (
        <>
            {glyph ? <View style={{ width: 22, alignItems: "center" }}>{glyph}</View> : null}
            <Text size={15.5} tone={tone ?? color.ink} numberOfLines={1} style={{ flex: 1, minWidth: 0 }}>
                {title}
                {quiet ? <Text size={13.5} tone={color.ink3}>{"  "}{quiet}</Text> : null}
            </Text>
            {when ? <Num size={13} tone={color.ink3}>{when}</Num> : null}
            {end}
        </>
    );
    const style = { flexDirection: "row" as const, alignItems: "center" as const, gap: 12, minHeight: TOUCH + 4, paddingHorizontal: 4, borderBottomWidth: 1, borderBottomColor: color.line };
    if (!onPress) return <View accessibilityLabel={label ?? title} style={style}>{body}</View>;
    return <Press role="link" label={label ?? title} onPress={onPress} onLongPress={onLongPress} style={style}>{body}</Press>;
}

/** A small heading over a part of a page. */
export function Heading({ children, count, right, first = false }: { children: string; count?: number | null; right?: ReactNode; first?: boolean }) {
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: first ? 4 : 22, marginBottom: 4, paddingHorizontal: 4 }}>
            <Text accessibilityRole="header" size={12.5} tone={color.ink3}>{children}</Text>
            {count !== undefined && count !== null && count > 0 ? <Num size={12.5} tone={color.ink3}>{count}</Num> : null}
            <View style={{ flex: 1 }} />
            {right}
        </View>
    );
}

/** Words that say what is so, quietly. */
export function Note({ children, tone, style }: { children: ReactNode; tone?: string; style?: object }) {
    return <Text size={14.5} tone={tone ?? color.ink2} style={[{ lineHeight: 20.5, paddingHorizontal: 4, paddingVertical: 6 }, style]}>{children}</Text>;
}

/** A read that failed, said where it happened, with a way to read again. */
export function Trouble({ text, onRetry }: { text: string; onRetry?: () => void }) {
    return (
        <View accessibilityRole="alert" style={{ flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 4, paddingVertical: 8 }}>
            <Text size={14.5} tone={color.amber} style={{ flex: 1, lineHeight: 20 }}>{text}</Text>
            {onRetry ? (
                <Press label="Try again" onPress={onRetry} style={{ minHeight: 36, justifyContent: "center" }}>
                    <Text size={14.5} tone={color.amber}>Try again</Text>
                </Press>
            ) : null}
        </View>
    );
}

/** A tag after a name: "Admin", "Paused", "Needs setup". */
export function Tag({ text, tone = "grey" }: { text: string; tone?: "grey" | "amber" | "violet" }) {
    const ink = tone === "amber" ? color.amber : tone === "violet" ? color.violetInk : color.ink2;
    const wash = tone === "amber" ? color.amberWash : tone === "violet" ? color.violetWash : color.wash;
    return (
        <View style={{ backgroundColor: wash, borderRadius: 999, paddingHorizontal: 8, paddingVertical: 2 }}>
            <Text size={12.5} tone={ink}>{text}</Text>
        </View>
    );
}

export function Tags({ tags }: { tags: { text: string; tone?: "grey" | "amber" | "violet" }[] }) {
    if (tags.length === 0) return null;
    return <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6 }}>{tags.map((tag) => <Tag key={tag.text} {...tag} />)}</View>;
}

/** What a step is, as a glyph: an agent's face, a person, code, a fork. */
export function StepGlyph({ kind, podId, agent }: { kind: string; podId: string; agent?: string | null }) {
    const tone = color.ink2;
    switch (kind) {
        case "AGENT": return <LemmaFace id={agent ? podId + ":" + agent : podId} size={22} />;
        case "FORM": return <Icon name="people" size={17} tone={tone} />;
        case "FUNCTION": return <Glyph name="code" size={16} tone={tone} />;
        case "DECISION": return <Glyph name="tree" size={16} tone={tone} />;
        case "LOOP": return <Icon name="refresh" size={16} tone={tone} />;
        case "WAIT_UNTIL": return <Icon name="clock" size={16} tone={tone} />;
        default: return <Icon name="automation" size={16} tone={tone} />;
    }
}

export const STATE_WORD: Record<NodeState, string> = {
    done: "Done", failed: "Failed", running: "Running", waiting: "Waiting",
    skipped: "Not reached", pending: "Not yet", cancelled: "Cancelled",
};

/** How a step stands, as a small mark beside its word. */
export function StateMark({ state }: { state: NodeState }) {
    if (state === "done") return <View style={{ width: 14, height: 14, borderRadius: 7, backgroundColor: color.wash, alignItems: "center", justifyContent: "center" }}><Icon name="check" size={10} width={2.4} tone={color.ink2} /></View>;
    if (state === "failed") return <View style={{ width: 14, height: 14, borderRadius: 7, backgroundColor: color.amberWash, alignItems: "center", justifyContent: "center" }}><Icon name="close" size={10} width={2.4} tone={color.amber} /></View>;
    if (state === "running") return <RunEnd mark="going" />;
    if (state === "waiting") return <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: color.violet, margin: 3 }} />;
    if (state === "cancelled") return <View style={{ width: 14, height: 14, borderRadius: 7, backgroundColor: color.wash, alignItems: "center", justifyContent: "center" }}><Icon name="close" size={10} width={2.4} tone={color.ink3} /></View>;
    return <View style={{ width: 12, height: 12, borderRadius: 6, borderWidth: 1.5, borderColor: color.line2, margin: 1 }} />;
}

/** A step's output read as a document: one row per key, prose kept as prose,
 *  the raw data a tap away (`workflow/data-view.tsx`). */
export function Fields({ value, lead, showLead = false }: { value: unknown; lead?: string | null; showLead?: boolean }) {
    const [raw, setRaw] = useState(false);
    const read = fieldsOf(value, lead ?? null);
    if (!read) return null;
    return (
        <View style={{ gap: 6 }}>
            {raw ? <Data text={JSON.stringify(value, null, 2)} /> : (
                <>
                    {showLead && read.lead ? <Prose text={read.lead} /> : null}
                    {read.whole ? <ShownValue value={read.whole} /> : null}
                    {read.fields.map((field) => (
                        <View key={field.label} style={{ gap: 2 }}>
                            <Text size={12.5} tone={color.ink3}>{field.label}</Text>
                            <ShownValue value={field.value} />
                        </View>
                    ))}
                </>
            )}
            <Press label={raw ? "Show as fields" : "Show the data"} onPress={() => setRaw((was) => !was)} style={{ alignSelf: "flex-start", minHeight: 32, justifyContent: "center" }}>
                <Text size={13} tone={color.ink3}>{raw ? "Show as fields" : "Show the data"}</Text>
            </Press>
        </View>
    );
}

function ShownValue({ value }: { value: Shown }) {
    if (value.kind === "prose") return <Prose text={value.text} />;
    if (value.kind === "data") return <Data text={value.text} />;
    return <Text size={14.5} selectable style={{ lineHeight: 20 }}>{value.text}</Text>;
}

/** A paragraph, folded when it is long. */
export function Prose({ text, lines = 8 }: { text: string; lines?: number }) {
    const [open, setOpen] = useState(false);
    const long = text.length > 480 || text.split("\n").length > lines;
    return (
        <View style={{ gap: 2 }}>
            <Text size={14.5} selectable numberOfLines={open || !long ? undefined : lines} style={{ lineHeight: 20.5 }}>{text}</Text>
            {long ? (
                <Press label={open ? "Show less" : "Show all"} onPress={() => setOpen((was) => !was)} style={{ alignSelf: "flex-start", minHeight: 32, justifyContent: "center" }}>
                    <Text size={13} tone={color.ink3}>{open ? "Show less" : "Show all"}</Text>
                </Press>
            ) : null}
        </View>
    );
}

/** Raw data, in a box that scrolls sideways rather than wrapping. */
export function Data({ text }: { text: string }) {
    return (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ backgroundColor: color.wash, borderRadius: 10 }} contentContainerStyle={{ padding: 10 }}>
            <Text selectable style={{ fontFamily: font.num, fontSize: 12.5, lineHeight: 18, color: color.ink2 }}>{text}</Text>
        </ScrollView>
    );
}

/** A choice of a few, as a row of chips that scrolls when it runs over. */
export function Choices<T extends string>({ options, value, onPick, label }: { options: { value: T; label: string }[]; value: T; onPick: (value: T) => void; label: string }) {
    return (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} accessibilityLabel={label} contentContainerStyle={{ gap: 6, paddingVertical: 2 }}>
            {options.map((one) => {
                const on = one.value === value;
                return (
                    <Press
                        key={one.value}
                        role="radio"
                        label={one.label}
                        accessibilityState={{ selected: on }}
                        onPress={() => onPick(one.value)}
                        hitSlop={{ top: 6, bottom: 6 }}
                        style={{ height: 34, paddingHorizontal: 12, borderRadius: 999, backgroundColor: on ? color.ink : color.paper, borderWidth: on ? 0 : 1, borderColor: color.line2, justifyContent: "center" }}
                    >
                        <Text size={13.5} tone={on ? color.onInk : color.ink} numberOfLines={1}>{one.label}</Text>
                    </Press>
                );
            })}
        </ScrollView>
    );
}
