import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ActivityIndicator, Platform, ScrollView, Switch, TextInput, View } from "react-native";
import DateTimePicker, { DateTimePickerAndroid, type DateTimePickerEvent } from "@react-native-community/datetimepicker";
import { Icon } from "../icons";
import { Button, Press } from "../press";
import { Sheet } from "../sheet";
import { Text } from "../text";
import { color, font, TOUCH } from "../tokens";
import { CHIP_LIMIT, dateOf, valueOf, type FormErrors, type FormField, type FormValues } from "./model";

/** A form drawn natively from `FormField`s (`model.ts`): a workflow's form,
 *  a table's record. The caller holds the values and builds the payload with
 *  the SDK; this draws each field the way a phone asks for it. */

export type LoadReferences = (reference: { table: string; column: string }, query: string) => Promise<{ label: string; value: string }[]>;

export function Form({ fields, values, errors = {}, onChange, loadReferences }: {
    fields: readonly FormField[];
    values: FormValues;
    errors?: FormErrors;
    onChange: (name: string, value: unknown) => void;
    /** Rows of another table to link to, for a reference field. */
    loadReferences?: LoadReferences;
}) {
    return (
        <View style={{ gap: 18 }}>
            {fields.map((field) => (
                <FieldInput key={field.name} field={field} value={values[field.name]} error={errors[field.name] ?? null} onChange={(next) => onChange(field.name, next)} loadReferences={loadReferences} />
            ))}
        </View>
    );
}

const BOX_HEIGHT = TOUCH + 4;

/** A field's outline. Built as it draws, not once at import: `color` holds
 *  whichever palette is in use, and a style made at import keeps the light
 *  one's line, which vanishes on a dark page. */
const box = () => ({
    borderWidth: 1,
    borderColor: color.line2,
    borderRadius: 12,
    paddingHorizontal: 12,
    minHeight: BOX_HEIGHT,
    justifyContent: "center",
} as const);

function Shell({ field, error, children }: { field: FormField; error: string | null; children: ReactNode }) {
    return (
        <View style={{ gap: 6 }}>
            <Text size={13.5} tone={color.ink2}>{field.label}{field.required ? <Text size={13.5} tone={color.ink3}> · needed</Text> : null}</Text>
            {children}
            {field.description ? <Text size={13} tone={color.ink3} style={{ lineHeight: 18 }}>{field.description}</Text> : null}
            {error ? <Text size={13} tone={color.amber}>{error}</Text> : null}
        </View>
    );
}

function FieldInput({ field, value, error, onChange, loadReferences }: {
    field: FormField;
    value: unknown;
    error: string | null;
    onChange: (value: unknown) => void;
    loadReferences?: LoadReferences;
}) {
    switch (field.kind) {
        case "boolean":
            return (
                <View style={{ gap: 6 }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: TOUCH }}>
                        <Text size={16} style={{ flex: 1 }}>{field.label}</Text>
                        <Switch
                            accessibilityLabel={field.label}
                            value={Boolean(value)}
                            disabled={field.readOnly}
                            onValueChange={onChange}
                            trackColor={{ false: color.track, true: color.ink }}
                            thumbColor={value ? color.paper : color.ink3}
                            ios_backgroundColor={color.track}
                        />
                    </View>
                    {field.description ? <Text size={13} tone={color.ink3}>{field.description}</Text> : null}
                    {error ? <Text size={13} tone={color.amber}>{error}</Text> : null}
                </View>
            );
        case "select":
            return <Shell field={field} error={error}><Choice field={field} value={String(value ?? "")} onChange={onChange} /></Shell>;
        case "date":
        case "datetime":
            return <Shell field={field} error={error}><DateInput field={field} value={String(value ?? "")} onChange={onChange} /></Shell>;
        case "reference":
            return <Shell field={field} error={error}><ReferenceInput field={field} value={String(value ?? "")} onChange={onChange} load={loadReferences} /></Shell>;
        default:
            return <Shell field={field} error={error}><Typed field={field} value={String(value ?? "")} onChange={onChange} /></Shell>;
    }
}

function Typed({ field, value, onChange }: { field: FormField; value: string; onChange: (value: string) => void }) {
    const long = field.kind === "textarea" || field.kind === "json";
    const input = <Box field={field} value={value} onChange={onChange} long={long} />;
    if (!field.suggestions?.length || field.readOnly) return input;
    return (
        <View style={{ gap: 8 }}>
            {input}
            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
                {field.suggestions.map((option) => {
                    const on = option === value;
                    return (
                        <Press key={option} role="radio" label={option} accessibilityState={{ selected: on }} onPress={() => onChange(option)} style={{ height: 32, paddingHorizontal: 12, borderRadius: 999, justifyContent: "center", backgroundColor: on ? color.ink : color.paper, borderWidth: on ? 0 : 1, borderColor: color.line2 }}>
                            <Text size={14} tone={on ? color.onInk : color.ink}>{option}</Text>
                        </Press>
                    );
                })}
            </View>
        </View>
    );
}

function Box({ field, value, onChange, long }: { field: FormField; value: string; onChange: (value: string) => void; long: boolean }) {
    return (
        <TextInput
            accessibilityLabel={field.label}
            value={value}
            editable={!field.readOnly}
            onChangeText={onChange}
            multiline={long}
            autoCapitalize={field.kind === "email" || field.kind === "json" ? "none" : "sentences"}
            autoCorrect={field.kind === "text" || field.kind === "textarea"}
            keyboardType={field.kind === "email" ? "email-address" : field.kind === "number" ? (field.integer ? "number-pad" : "decimal-pad") : "default"}
            placeholderTextColor={color.ink3}
            style={{
                ...box(),
                paddingVertical: long ? 10 : 0,
                minHeight: long ? 96 : BOX_HEIGHT,
                textAlignVertical: long ? "top" : "center",
                fontFamily: field.kind === "json" ? Platform.select({ ios: "Menlo", default: "monospace" }) : field.kind === "number" ? font.num : font.regular,
                fontSize: field.kind === "json" ? 14 : 16,
                color: field.readOnly ? color.ink3 : color.ink,
            }}
        />
    );
}

/** A few choices as chips; more as a list to pick from. */
function Choice({ field, value, onChange }: { field: FormField; value: string; onChange: (value: string) => void }) {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState("");
    const chosen = field.options.find((option) => option.value === value) ?? null;
    if (field.options.length <= CHIP_LIMIT) {
        return (
            <View accessibilityRole="radiogroup" style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
                {field.options.map((option) => {
                    const on = option.value === value;
                    return (
                        <Press key={option.value} role="radio" label={option.label} accessibilityState={{ selected: on, disabled: field.readOnly }} disabled={field.readOnly} onPress={() => onChange(on && !field.required ? "" : option.value)} style={{ height: 36, paddingHorizontal: 14, borderRadius: 999, justifyContent: "center", backgroundColor: on ? color.ink : color.paper, borderWidth: on ? 0 : 1, borderColor: color.line2 }}>
                            <Text size={14.5} tone={on ? color.onInk : color.ink}>{option.label}</Text>
                        </Press>
                    );
                })}
            </View>
        );
    }
    const shown = query ? field.options.filter((option) => option.label.toLowerCase().includes(query.toLowerCase())) : field.options;
    return (
        <>
            <PickerRow label={field.label} said={chosen?.label ?? null} disabled={field.readOnly} onPress={() => setOpen(true)} />
            <Sheet open={open} onClose={() => setOpen(false)} title={field.label} tall>
                <SheetSearch value={query} onChange={setQuery} />
                <ScrollView keyboardShouldPersistTaps="handled">
                    {shown.map((option) => (
                        <OptionRow key={option.value} label={option.label} on={option.value === value} onPress={() => { onChange(option.value); setOpen(false); }} />
                    ))}
                </ScrollView>
            </Sheet>
        </>
    );
}

function PickerRow({ label, said, disabled, onPress, busy = false }: { label: string; said: string | null; disabled?: boolean; onPress: () => void; busy?: boolean }) {
    return (
        <Press label={`${label}: ${said ?? "not chosen"}. Change`} disabled={disabled} onPress={onPress} style={{ ...box(), flexDirection: "row", alignItems: "center", gap: 10 }}>
            <Text size={16} tone={said ? (disabled ? color.ink3 : color.ink) : color.ink3} numberOfLines={1} style={{ flex: 1 }}>{said ?? "Choose"}</Text>
            {busy ? <ActivityIndicator size="small" color={color.ink3} /> : <Icon name="down" size={16} tone={color.ink3} />}
        </Press>
    );
}

function SheetSearch({ value, onChange }: { value: string; onChange: (value: string) => void }) {
    return (
        <View style={{ paddingHorizontal: 16, paddingBottom: 8 }}>
            <TextInput accessibilityLabel="Search" value={value} onChangeText={onChange} placeholder="Search" placeholderTextColor={color.ink3} autoCorrect={false} style={{ ...box(), fontFamily: font.regular, fontSize: 16, color: color.ink }} />
        </View>
    );
}

function OptionRow({ label, on, onPress }: { label: string; on: boolean; onPress: () => void }) {
    return (
        <Press label={label} accessibilityState={{ selected: on }} onPress={onPress} style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: 52, paddingHorizontal: 20 }}>
            <Text size={16} style={{ flex: 1 }} numberOfLines={2}>{label}</Text>
            {on ? <Icon name="check" size={18} width={2} /> : null}
        </Press>
    );
}

function spoken(date: Date, withTime: boolean): string {
    const day = date.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
    return withTime ? `${day}, ${date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}` : day;
}

function DateInput({ field, value, onChange }: { field: FormField; value: string; onChange: (value: string) => void }) {
    const withTime = field.kind === "datetime";
    const current = dateOf(value);
    const [open, setOpen] = useState(false);
    const [draft, setDraft] = useState<Date>(current ?? new Date());
    const pick = () => {
        if (Platform.OS === "android") {
            /* Android's own dialogs: the date, then the time when there is one. */
            DateTimePickerAndroid.open({
                value: current ?? new Date(),
                mode: "date",
                onChange: (event: DateTimePickerEvent, date?: Date) => {
                    if (event.type !== "set" || !date) return;
                    if (!withTime) { onChange(valueOf(date, false)); return; }
                    DateTimePickerAndroid.open({
                        value: date,
                        mode: "time",
                        onChange: (timed: DateTimePickerEvent, time?: Date) => {
                            if (timed.type === "set" && time) onChange(valueOf(time, true));
                        },
                    });
                },
            });
            return;
        }
        setDraft(current ?? new Date());
        setOpen(true);
    };
    return (
        <>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                <View style={{ flex: 1 }}>
                    <PickerRow label={field.label} said={current ? spoken(current, withTime) : null} disabled={field.readOnly} onPress={pick} />
                </View>
                {current && !field.required && !field.readOnly ? (
                    <Press label={`Clear ${field.label}`} onPress={() => onChange("")} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                        <Icon name="close" size={18} tone={color.ink3} />
                    </Press>
                ) : null}
            </View>
            {Platform.OS === "ios" ? (
                <Sheet open={open} onClose={() => setOpen(false)} title={field.label}>
                    <View style={{ alignItems: "center", paddingHorizontal: 12 }}>
                        <DateTimePicker value={draft} mode={withTime ? "datetime" : "date"} display={withTime ? "spinner" : "inline"} onChange={(_, date) => date && setDraft(date)} />
                    </View>
                    <View style={{ paddingHorizontal: 20, paddingTop: 8 }}>
                        <Button title="Done" height={48} onPress={() => { onChange(valueOf(draft, withTime)); setOpen(false); }} />
                    </View>
                </Sheet>
            ) : null}
        </>
    );
}

/** A link to another table's row: the rows to pick from come from the
 *  caller, which knows how to read them. */
function ReferenceInput({ field, value, onChange, load }: { field: FormField; value: string; onChange: (value: string) => void; load?: LoadReferences }) {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState("");
    const [rows, setRows] = useState<{ label: string; value: string }[] | null>(null);
    const [known, setKnown] = useState<{ label: string; value: string }[]>([]);
    const reference = field.reference;
    useEffect(() => {
        if (!load || !reference) return;
        let live = true;
        const timer = setTimeout(() => {
            void load(reference, open ? query : "").then((found) => {
                if (!live) return;
                if (open) setRows(found);
                setKnown((was) => [...was, ...found.filter((one) => !was.some((held) => held.value === one.value))]);
            }, () => { if (live && open) setRows([]); });
        }, open && query ? 250 : 0);
        return () => { live = false; clearTimeout(timer); };
    }, [load, reference, open, query]);
    const label = useMemo(() => known.find((one) => one.value === value)?.label ?? (value ? value.slice(0, 8) + "…" : null), [known, value]);
    if (!load || !reference) return <Typed field={{ ...field, kind: "text" }} value={value} onChange={onChange} />;
    return (
        <>
            <PickerRow label={field.label} said={label} disabled={field.readOnly} onPress={() => { setRows(null); setOpen(true); }} />
            <Sheet open={open} onClose={() => setOpen(false)} title={field.label} tall>
                <SheetSearch value={query} onChange={setQuery} />
                <ScrollView keyboardShouldPersistTaps="handled">
                    {rows === null ? <ActivityIndicator color={color.ink3} style={{ paddingVertical: 20 }} /> : null}
                    {rows?.length === 0 ? <Text size={15} tone={color.ink3} style={{ paddingHorizontal: 20, paddingVertical: 12 }}>Nothing found.</Text> : null}
                    {!field.required && value ? <OptionRow label="None" on={false} onPress={() => { onChange(""); setOpen(false); }} /> : null}
                    {(rows ?? []).map((row) => (
                        <OptionRow key={row.value} label={row.label} on={row.value === value} onPress={() => { onChange(row.value); setOpen(false); }} />
                    ))}
                </ScrollView>
            </Sheet>
        </>
    );
}
