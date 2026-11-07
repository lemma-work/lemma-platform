import { createContext, useContext, useState, type ReactNode } from "react";
import { KeyboardAvoidingView, Platform, ScrollView, TextInput, View } from "react-native";
import { Icon } from "../../ui/icons";
import { Button, Press } from "../../ui/press";
import { Sheet } from "../../ui/sheet";
import { Num, Text } from "../../ui/text";
import { color, font, TOUCH } from "../../ui/tokens";
import { cellText, type Row } from "@web/library/record-cache";
import type { ColumnProfile } from "@web/library/profile";

/** The small pieces every layout of a table is drawn with. */

/** A value from a few, as read: a status, a source. */
export function Tag({ text }: { text: string }) {
    return (
        <View style={{ backgroundColor: color.wash, borderRadius: 999, paddingHorizontal: 8, paddingVertical: 2, flexShrink: 1 }}>
            <Text size={12.5} tone={color.ink2} numberOfLines={1} style={{ fontFamily: /^\d/.test(text) ? font.num : font.regular }}>{text}</Text>
        </View>
    );
}

/** The values a table's schema fixes for a column (an ENUM's options), by
 *  column. A mover offers them all, even one no row holds yet, and offers no
 *  "another" the table would refuse. Given by the screen that read the schema. */
export const FixedValues = createContext<Map<string, string[]>>(new Map());

/** A status, as the thing you change rather than the thing you read: the
 *  value, and a sheet of the values this table holds, plus another. */
export function Mover({ column, row, onChange, wide = false }: { column: ColumnProfile; row: Row; onChange: (row: Row, changes: Row) => void; wide?: boolean }) {
    const [open, setOpen] = useState(false);
    const current = cellText(row[column.name]);
    return (
        <>
            <Press
                label={`${column.name.replace(/_/g, " ")}: ${current}. Change`}
                onPress={() => setOpen(true)}
                hitSlop={8}
                style={{ flexDirection: "row", alignItems: "center", gap: 3, height: wide ? 34 : 28, paddingLeft: 10, paddingRight: 7, borderRadius: 999, borderWidth: 1, borderColor: color.line2, flexShrink: 1 }}
            >
                <Text size={wide ? 14 : 13} numberOfLines={1} style={{ flexShrink: 1 }}>{current}</Text>
                <Icon name="down" size={13} tone={color.ink3} />
            </Press>
            <MoveSheet open={open} onClose={() => setOpen(false)} column={column} current={current} onPick={(value) => { setOpen(false); if (value !== current) onChange(row, { [column.name]: value }); }} />
        </>
    );
}

/** The values a status can move to: the ones the table's rows hold, since a
 *  stage stored as text still only ever holds a few, and a way to add one. */
export function MoveSheet({ open, onClose, column, current, onPick }: { open: boolean; onClose: () => void; column: ColumnProfile; current: string; onPick: (value: string) => void }) {
    const [typing, setTyping] = useState<string | null>(null);
    const fixed = useContext(FixedValues).get(column.name) ?? null;
    const held = fixed ? [...fixed, ...column.values.filter((value) => !fixed.includes(value))] : column.values;
    const values = held.includes(current) || current === "—" ? held : [current, ...held];
    return (
        <Sheet open={open} onClose={() => { setTyping(null); onClose(); }} title={column.name.replace(/_/g, " ")}>
            <ScrollView keyboardShouldPersistTaps="handled">
                {values.map((value) => (
                    <Press key={value} role="radio" label={value} accessibilityState={{ selected: value === current }} onPress={() => onPick(value)} style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: 52, paddingHorizontal: 20 }}>
                        <Text size={16} style={{ flex: 1 }} numberOfLines={1}>{value}</Text>
                        {value === current ? <Icon name="check" size={18} width={2} /> : null}
                    </Press>
                ))}
                {fixed ? null : typing === null ? (
                    <Press label="Another value" onPress={() => setTyping("")} style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 52, paddingHorizontal: 20 }}>
                        <Icon name="plus" size={17} tone={color.ink2} />
                        <Text size={16} tone={color.ink2}>Another…</Text>
                    </Press>
                ) : (
                    <View style={{ flexDirection: "row", gap: 8, paddingHorizontal: 16, paddingVertical: 8 }}>
                        <TextInput
                            accessibilityLabel={`New ${column.name}`}
                            autoFocus
                            value={typing}
                            onChangeText={setTyping}
                            onSubmitEditing={() => { const next = typing.trim(); setTyping(null); if (next) onPick(next); }}
                            returnKeyType="done"
                            autoCapitalize="none"
                            placeholder={`A new ${column.name.replace(/_/g, " ")}`}
                            placeholderTextColor={color.ink3}
                            style={{ flex: 1, minHeight: TOUCH, borderWidth: 1, borderColor: color.line2, borderRadius: 12, paddingHorizontal: 12, fontFamily: font.regular, fontSize: 16, color: color.ink }}
                        />
                        <Button title="Move" onPress={() => { const next = (typing ?? "").trim(); setTyping(null); if (next) onPick(next); }} />
                    </View>
                )}
            </ScrollView>
        </Sheet>
    );
}

/** A tick that is the control: a row of a checklist, done or not. */
export function Tick({ done, label, onPress }: { done: boolean; label: string; onPress?: () => void }) {
    const box = (
        <View style={{ width: 22, height: 22, borderRadius: 7, borderWidth: done ? 0 : 1.5, borderColor: color.ink3, backgroundColor: done ? color.ink : "transparent", alignItems: "center", justifyContent: "center" }}>
            {done ? <Icon name="check" size={15} width={2.4} tone={color.onInk} /> : null}
        </View>
    );
    /* Without a tick to make, never an empty box: it would be the most
       inviting thing on the row and do nothing. A done row keeps its mark. */
    if (!onPress) {
        return (
            <View accessibilityElementsHidden importantForAccessibility="no" style={{ width: TOUCH - 10, alignItems: "center" }}>
                {done ? <Icon name="check" size={17} width={2.2} tone={color.ink3} /> : null}
            </View>
        );
    }
    return (
        <Press role="button" label={label} accessibilityRole="checkbox" accessibilityState={{ checked: done }} onPress={onPress} hitSlop={10} style={{ width: TOUCH - 10, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
            {box}
        </Press>
    );
}

/** A small heading over a group of rows, with its count. */
export function GroupHead({ text, count, tone = color.ink3, right }: { text: string; count?: number; tone?: string; right?: ReactNode }) {
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: 18, marginBottom: 2, paddingHorizontal: 4 }}>
            <Text size={12.5} tone={tone}>{text}</Text>
            {count !== undefined ? <Num size={12.5} tone={color.ink3}>{count}</Num> : null}
            <View style={{ flex: 1 }} />
            {right}
        </View>
    );
}

/** "Add" at the foot of a group, or a quiet link anywhere. */
export function Quiet({ text, onPress, label, tone = color.ink2 }: { text: string; onPress: () => void; label?: string; tone?: string }) {
    return (
        <Press label={label ?? text} onPress={onPress} style={{ alignSelf: "flex-start", minHeight: 40, justifyContent: "center", paddingHorizontal: 4 }}>
            <Text size={14} tone={tone}>{text}</Text>
        </Press>
    );
}

export function SearchBox({ value, onChange, placeholder }: { value: string; onChange: (next: string) => void; placeholder: string }) {
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8, height: 40, borderRadius: 12, backgroundColor: color.wash, paddingHorizontal: 12 }}>
            <Icon name="search" size={17} tone={color.ink3} />
            <TextInput
                accessibilityLabel={placeholder}
                value={value}
                onChangeText={onChange}
                placeholder={placeholder}
                placeholderTextColor={color.ink3}
                autoCorrect={false}
                returnKeyType="search"
                style={{ flex: 1, height: 40, fontFamily: font.regular, fontSize: 15.5, color: color.ink, padding: 0 }}
            />
            {value ? (
                <Press label="Clear the search" onPress={() => onChange("")} hitSlop={10}>
                    <Icon name="close" size={16} tone={color.ink3} />
                </Press>
            ) : null}
        </View>
    );
}

/** A chip in a row of them: chosen in ink, otherwise outlined. */
export function Chip({ text, on, onPress, label, count }: { text: string; on: boolean; onPress: () => void; label?: string; count?: number }) {
    return (
        <Press
            label={label ?? text}
            accessibilityState={{ selected: on }}
            onPress={onPress}
            hitSlop={{ top: 6, bottom: 6 }}
            style={{ height: 32, paddingHorizontal: 12, borderRadius: 999, backgroundColor: on ? color.ink : color.paper, borderWidth: on ? 0 : 1, borderColor: color.line2, flexDirection: "row", alignItems: "center", gap: 4 }}
        >
            <Text size={13.5} tone={on ? color.onInk : color.ink} numberOfLines={1}>{text}</Text>
            {count !== undefined && count > 0 ? <Num size={13.5} tone={on ? color.onInk : color.ink}>{count}</Num> : null}
        </Press>
    );
}

export interface Action {
    label: string;
    onPress: () => void;
    tone?: "danger";
}

/** How long a sheet takes to go, before the next one may come: iOS will not
 *  show a sheet while another is still leaving. */
const SHEET_GONE_MS = 300;

/** What can be done to one thing, from the bottom. */
export function ActionSheet({ open, onClose, title, actions }: { open: boolean; onClose: () => void; title?: string; actions: Action[] }) {
    return (
        <Sheet open={open} onClose={onClose} title={title}>
            {actions.map((action) => (
                <Press key={action.label} label={action.label} onPress={() => { onClose(); setTimeout(action.onPress, SHEET_GONE_MS); }} style={{ minHeight: 52, justifyContent: "center", paddingHorizontal: 20 }}>
                    <Text size={16} tone={action.tone === "danger" ? color.amber : color.ink}>{action.label}</Text>
                </Press>
            ))}
        </Sheet>
    );
}

/** Words for the Lemma, started here and finished by the person: the web
 *  puts them in the chat box; the phone's chat sends what it is opened with,
 *  so the finishing happens in this sheet first. */
export function AskSheet({ open, onClose, name, start, onSend }: { open: boolean; onClose: () => void; name: string; start: string; onSend: (text: string) => void }) {
    const [text, setText] = useState(start);
    const [was, setWas] = useState(open);
    if (open !== was) {
        setWas(open);
        if (open) setText(start);
    }
    const ready = text.trim().length > 0;
    return (
        <Sheet open={open} onClose={onClose} title={`Ask ${name}`}>
            <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ paddingHorizontal: 16, gap: 12 }}>
                <TextInput
                    accessibilityLabel={`What to ask ${name}`}
                    autoFocus
                    multiline
                    value={text}
                    onChangeText={setText}
                    placeholderTextColor={color.ink3}
                    style={{ minHeight: 96, maxHeight: 200, borderWidth: 1, borderColor: color.line2, borderRadius: 14, paddingHorizontal: 12, paddingVertical: 10, textAlignVertical: "top", fontFamily: font.regular, fontSize: 16, lineHeight: 22, color: color.ink }}
                />
                <Button title="Send" disabled={!ready} onPress={() => { onClose(); onSend(text.trim()); }} />
            </KeyboardAvoidingView>
        </Sheet>
    );
}

/** Asking before something that cannot be taken back. */
export function ConfirmSheet({ open, onClose, title, say, yes, onYes, busy = false }: { open: boolean; onClose: () => void; title: string; say: string; yes: string; onYes: () => void; busy?: boolean }) {
    return (
        <Sheet open={open} onClose={onClose} title={title}>
            <View style={{ paddingHorizontal: 20, gap: 16, paddingBottom: 4 }}>
                <Text size={16} style={{ lineHeight: 22 }}>{say}</Text>
                <View style={{ flexDirection: "row", gap: 8 }}>
                    <Button title={yes} grow busy={busy} onPress={onYes} />
                    <Button title="Keep it" kind="outline" grow onPress={onClose} />
                </View>
            </View>
        </Sheet>
    );
}

/** A write that did not land, said where it happened, with a way to dismiss. */
export function Problem({ text, onDismiss }: { text: string; onDismiss: () => void }) {
    return (
        <View accessibilityRole="alert" style={{ flexDirection: "row", alignItems: "flex-start", gap: 8, marginTop: 10, padding: 12, borderRadius: 12, backgroundColor: color.amberWash }}>
            <Text size={14} tone={color.amber} style={{ flex: 1, lineHeight: 19 }}>{text}</Text>
            <Press label="Dismiss" onPress={onDismiss} hitSlop={10}>
                <Icon name="close" size={15} tone={color.amber} />
            </Press>
        </View>
    );
}
