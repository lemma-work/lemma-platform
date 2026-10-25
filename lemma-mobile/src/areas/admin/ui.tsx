import { useState, type ReactNode } from "react";
import { ActivityIndicator, KeyboardAvoidingView, Platform, ScrollView, Switch, TextInput, View, type KeyboardTypeOptions } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { copyText } from "../../thread/clipboard";
import { BackLink } from "../../ui/chrome";
import { Icon, type IconName } from "../../ui/icons";
import { Button, Press } from "../../ui/press";
import { Sheet } from "../../ui/sheet";
import { Num, Text } from "../../ui/text";
import { color, font, TOUCH } from "../../ui/tokens";
import { namesTeammate } from "@web/space/confirm-name";

export { count, firstFew, isForbidden, isMissing, problemOf, statusOf } from "./words";

/** The pieces every settings screen is made of, so a Lemma's, the account's
 *  and the organization's settings read as one place: a screen with its way
 *  back, a heading and the one line under it; groups of rows under a small
 *  caption, rules between them and no boxes; a row is one dense line that
 *  says its value at the end; a switch; a choice in a sheet; a text box; a
 *  line that says what just happened; a confirmation for what cannot be
 *  undone; a value to copy.
 *
 *  Colours are read from `color` as each piece draws, never kept in a style
 *  made at import, so every piece holds in dark. */

/** A settings screen: back, heading, its note, then what scrolls. */
export function SettingsScreen({ title, note, back, right, children, scroll = true }: {
    title: string;
    note?: string | null;
    /** Where Back goes, by name ("Account", "Support"). */
    back?: string;
    right?: ReactNode;
    children: ReactNode;
    scroll?: boolean;
}) {
    const head = (
        <View style={{ paddingHorizontal: 20, paddingTop: 4, gap: 6, paddingBottom: 8 }}>
            <Text accessibilityRole="header" size={24} weight="medium" style={{ letterSpacing: -0.24 }}>{title}</Text>
            {note ? <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20 }}>{note}</Text> : null}
        </View>
    );
    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <View style={{ flexDirection: "row", alignItems: "center", paddingTop: 4, paddingLeft: 6, paddingRight: 8 }}>
                <BackLink label={back} />
                <View style={{ flex: 1 }} />
                {right}
            </View>
            <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
                {scroll ? (
                    <ScrollView contentContainerStyle={{ paddingBottom: 32 }} keyboardShouldPersistTaps="handled">
                        {head}
                        <View style={{ paddingHorizontal: 20, gap: 22, paddingTop: 6 }}>{children}</View>
                    </ScrollView>
                ) : (
                    <View style={{ flex: 1 }}>
                        {head}
                        <View style={{ flex: 1, paddingHorizontal: 20, paddingTop: 6 }}>{children}</View>
                    </View>
                )}
            </KeyboardAvoidingView>
        </SafeAreaView>
    );
}

/** Rows under a small caption, with a quiet line under them when one helps. */
export function Group({ label, foot, right, children }: { label?: string; foot?: ReactNode; right?: ReactNode; children: ReactNode }) {
    return (
        <View style={{ gap: 2 }}>
            {label || right ? (
                <View style={{ flexDirection: "row", alignItems: "center", minHeight: 22 }}>
                    <Text size={12.5} tone={color.ink3} style={{ flex: 1 }}>{label}</Text>
                    {right}
                </View>
            ) : null}
            {children}
            {typeof foot === "string" ? <Text size={13} tone={color.ink3} style={{ paddingTop: 6, lineHeight: 18 }}>{foot}</Text> : foot ?? null}
        </View>
    );
}

/** One line: what it is, what it is set to, and a way in when it opens.
 *  `hint` puts a second, quieter line under the label for what a word
 *  alone would not say. `needs` is the violet dot: somebody has to act. */
export function Row({ label, value, hint, onPress, glyph, end, needs = false, danger = false, disabled = false, role, last = false }: {
    label: string;
    value?: string | null;
    hint?: string | null;
    onPress?: () => void;
    glyph?: ReactNode;
    end?: ReactNode;
    needs?: boolean;
    danger?: boolean;
    disabled?: boolean;
    role?: "button" | "link";
    /** The last row of a group draws no rule under it. */
    last?: boolean;
}) {
    const body = (
        <>
            {glyph ? <View style={{ width: 28, alignItems: "center" }}>{glyph}</View> : null}
            <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
                <Text size={16} numberOfLines={1} tone={danger ? color.amber : color.ink}>{label}</Text>
                {hint ? <Text size={13} tone={color.ink3} numberOfLines={2}>{hint}</Text> : null}
            </View>
            {needs ? <View accessibilityLabel="Needs you" style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: color.violet }} /> : null}
            {value ? <Text size={14.5} tone={color.ink3} numberOfLines={1} style={{ maxWidth: "48%", textAlign: "right" }}>{value}</Text> : null}
            {end}
            {onPress && !end ? <Icon name="forward" size={16} tone={color.ink3} /> : null}
        </>
    );
    const style = {
        flexDirection: "row" as const,
        alignItems: "center" as const,
        gap: 10,
        minHeight: hint ? 58 : 50,
        paddingVertical: 6,
        borderBottomWidth: last ? 0 : 1,
        borderBottomColor: color.line,
        opacity: disabled ? 0.45 : 1,
    };
    if (!onPress) return <View accessibilityLabel={value ? `${label}, ${value}` : label} style={style}>{body}</View>;
    return (
        <Press role={role ?? "button"} label={value ? `${label}, ${value}` : label} disabled={disabled} onPress={onPress} style={style}>
            {body}
        </Press>
    );
}

/** A row that is a switch. `busy` while the change is being saved. */
export function ToggleRow({ label, hint, value, onChange, busy = false, disabled = false, last = false }: {
    label: string;
    hint?: string | null;
    value: boolean;
    onChange: (next: boolean) => void;
    busy?: boolean;
    disabled?: boolean;
    last?: boolean;
}) {
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: hint ? 58 : 50, paddingVertical: 6, borderBottomWidth: last ? 0 : 1, borderBottomColor: color.line }}>
            <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
                <Text size={16}>{label}</Text>
                {hint ? <Text size={13} tone={color.ink3} style={{ lineHeight: 18 }}>{hint}</Text> : null}
            </View>
            {busy ? <ActivityIndicator size="small" color={color.ink3} /> : null}
            <Switch
                accessibilityLabel={label}
                value={value}
                disabled={disabled || busy}
                onValueChange={onChange}
                trackColor={{ false: color.track, true: color.ink }}
                thumbColor={value ? color.paper : color.ink3}
            />
        </View>
    );
}

export interface Choice<T extends string = string> {
    value: T;
    label: string;
    hint?: string | null;
    disabled?: boolean;
}

/** One of a few, picked in a sheet from the bottom: a tick on the one held. */
export function ChoiceSheet<T extends string>({ open, title, choices, value, onPick, onClose }: {
    open: boolean;
    title: string;
    choices: readonly Choice<T>[];
    value: T | null;
    onPick: (value: T) => void;
    onClose: () => void;
}) {
    return (
        <Sheet open={open} onClose={onClose} title={title}>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 8 }}>
                {choices.map((choice, index) => {
                    const on = choice.value === value;
                    return (
                        <Press
                            key={choice.value}
                            role="radio"
                            label={choice.label}
                            disabled={choice.disabled}
                            accessibilityState={{ selected: on, disabled: choice.disabled }}
                            onPress={() => { onPick(choice.value); onClose(); }}
                            style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: choice.hint ? 58 : 50, paddingVertical: 6, borderBottomWidth: index === choices.length - 1 ? 0 : 1, borderBottomColor: color.line, opacity: choice.disabled ? 0.45 : 1 }}
                        >
                            <View style={{ flex: 1, gap: 1 }}>
                                <Text size={16}>{choice.label}</Text>
                                {choice.hint ? <Text size={13} tone={color.ink3} style={{ lineHeight: 18 }}>{choice.hint}</Text> : null}
                            </View>
                            {on ? <Icon name="check" size={18} width={2} /> : null}
                        </Press>
                    );
                })}
            </ScrollView>
        </Sheet>
    );
}

/** A row that opens a `ChoiceSheet`, saying the choice it holds. */
export function ChoiceRow<T extends string>({ label, hint, choices, value, onPick, disabled = false, last = false, title }: {
    label: string;
    hint?: string | null;
    choices: readonly Choice<T>[];
    value: T | null;
    onPick: (value: T) => void;
    disabled?: boolean;
    last?: boolean;
    title?: string;
}) {
    const [open, setOpen] = useState(false);
    const held = choices.find((one) => one.value === value);
    return (
        <>
            <Row label={label} hint={hint} value={held?.label ?? null} onPress={() => setOpen(true)} disabled={disabled} last={last} />
            <ChoiceSheet open={open} title={title ?? label} choices={choices} value={value} onPick={onPick} onClose={() => setOpen(false)} />
        </>
    );
}

/** A text box with its label above and its problem, or its hint, under it. */
export function Field({ label, value, onChange, placeholder, hint, error, multiline = false, keyboardType, autoFocus = false, secure = false, autoCapitalize, editable = true, onSubmit, maxLength }: {
    label?: string;
    value: string;
    onChange: (next: string) => void;
    placeholder?: string;
    hint?: string | null;
    error?: string | null;
    multiline?: boolean;
    keyboardType?: KeyboardTypeOptions;
    autoFocus?: boolean;
    /** A secret: drawn as dots, never offered to autofill. */
    secure?: boolean;
    autoCapitalize?: "none" | "sentences" | "words";
    editable?: boolean;
    onSubmit?: () => void;
    maxLength?: number;
}) {
    return (
        <View style={{ gap: 6 }}>
            {label ? <Text size={13.5} tone={color.ink2}>{label}</Text> : null}
            <TextInput
                accessibilityLabel={label ?? placeholder}
                value={value}
                onChangeText={onChange}
                placeholder={placeholder}
                placeholderTextColor={color.ink3}
                multiline={multiline}
                keyboardType={keyboardType}
                autoFocus={autoFocus}
                secureTextEntry={secure}
                autoComplete={secure ? "off" : undefined}
                textContentType={secure ? "none" : undefined}
                autoCorrect={!secure && keyboardType !== "email-address" && keyboardType !== "url"}
                autoCapitalize={autoCapitalize ?? (keyboardType === "email-address" || keyboardType === "url" || secure ? "none" : "sentences")}
                editable={editable}
                onSubmitEditing={onSubmit}
                returnKeyType={onSubmit ? "done" : undefined}
                maxLength={maxLength}
                style={{
                    borderWidth: 1,
                    borderColor: error ? color.amber : color.line2,
                    borderRadius: 12,
                    paddingHorizontal: 12,
                    paddingVertical: multiline ? 10 : 0,
                    minHeight: multiline ? 92 : TOUCH + 4,
                    textAlignVertical: multiline ? "top" : "center",
                    fontFamily: font.regular,
                    fontSize: 16,
                    color: editable ? color.ink : color.ink2,
                }}
            />
            {error ? <Text size={13} tone={color.amber}>{error}</Text> : hint ? <Text size={13} tone={color.ink3} style={{ lineHeight: 18 }}>{hint}</Text> : null}
        </View>
    );
}

/** A line that says what happened, or what is wrong: quiet grey, amber for
 *  a problem, ink for something done. */
export function Say({ children, tone = "quiet" }: { children: ReactNode; tone?: "quiet" | "bad" | "done" }) {
    const ink = tone === "bad" ? color.amber : tone === "done" ? color.ink : color.ink2;
    return (
        <Text accessibilityRole={tone === "bad" ? "alert" : undefined} accessibilityLiveRegion="polite" size={14} tone={ink} style={{ lineHeight: 20 }}>
            {children}
        </Text>
    );
}

/** A failed read, with its way to try again. */
export function ReadProblem({ said, onRetry }: { said: string; onRetry?: () => void }) {
    return (
        <View style={{ gap: 8, paddingVertical: 6 }}>
            <Say tone="bad">{said}</Say>
            {onRetry ? (
                <Press label="Try again" onPress={onRetry} style={{ alignSelf: "flex-start", minHeight: 36, justifyContent: "center" }}>
                    <Text size={14.5} weight="medium">Try again</Text>
                </Press>
            ) : null}
        </View>
    );
}

/** A quiet spinner where a group's rows will be. */
export function Reading({ label = "Loading" }: { label?: string }) {
    return (
        <View accessibilityLabel={label} style={{ paddingVertical: 14, alignItems: "flex-start" }}>
            <ActivityIndicator color={color.ink3} />
        </View>
    );
}

/** Before anything that cannot be undone: a sheet that says what will go,
 *  and, for the gravest, a word to type first. */
export function Confirm({ open, title, body, confirm, onConfirm, onClose, busy = false, error = null, typeToConfirm = null }: {
    open: boolean;
    title: string;
    body: string;
    confirm: string;
    onConfirm: () => void;
    onClose: () => void;
    busy?: boolean;
    error?: string | null;
    /** The word the person types before the button wakes, such as a name. */
    typeToConfirm?: string | null;
}) {
    const [typed, setTyped] = useState("");
    const ready = !typeToConfirm || namesTeammate(typed, typeToConfirm);
    return (
        <Sheet open={open} onClose={() => { setTyped(""); onClose(); }}>
            {/* A sheet is a window of its own: on iOS nothing moves it out of
                the keyboard's way, and the name box sits at its foot. Android
                resizes the window itself. */}
            <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
            <View style={{ paddingHorizontal: 20, gap: 14, paddingBottom: 6 }}>
                <Text size={18} weight="medium">{title}</Text>
                <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>{body}</Text>
                {typeToConfirm ? (
                    <Field label={`Type ${typeToConfirm} to confirm`} value={typed} onChange={setTyped} autoCapitalize="none" />
                ) : null}
                {error ? <Say tone="bad">{error}</Say> : null}
                <View style={{ flexDirection: "row", gap: 10 }}>
                    <Button title="Cancel" kind="outline" grow onPress={() => { setTyped(""); onClose(); }} />
                    <Button title={confirm} grow busy={busy} disabled={!ready} onPress={onConfirm} />
                </View>
            </View>
            </KeyboardAvoidingView>
        </Sheet>
    );
}

/** A value somebody copies elsewhere: a link, an address, a code. */
export function Copyable({ value, label, mono = false }: { value: string; label: string; mono?: boolean }) {
    const [copied, setCopied] = useState(false);
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 50, borderBottomWidth: 1, borderBottomColor: color.line }}>
            {mono ? (
                <Num size={14} tone={color.ink} numberOfLines={2} style={{ flex: 1 }} selectable>{value}</Num>
            ) : (
                <Text size={15} numberOfLines={2} style={{ flex: 1 }} selectable>{value}</Text>
            )}
            <Press label={`Copy ${label}`} onPress={() => { copyText(value); setCopied(true); setTimeout(() => setCopied(false), 1600); }} style={{ minHeight: TOUCH, justifyContent: "center", paddingHorizontal: 6 }}>
                <Text size={14.5} weight="medium">{copied ? "Copied" : "Copy"}</Text>
            </Press>
        </View>
    );
}

/** A glyph for a row's left edge, in the quiet ink rows use. */
export function RowIcon({ name }: { name: IconName }) {
    return <Icon name={name} size={18} tone={color.ink2} />;
}
