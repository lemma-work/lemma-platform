import { useState } from "react";
import { Switch, TextInput, View } from "react-native";
import { Icon } from "../../../ui/icons";
import { Press } from "../../../ui/press";
import { Text } from "../../../ui/text";
import { color, font, TOUCH } from "../../../ui/tokens";
import { ChoiceRow } from "../ui";
import { REDACTED, unchangedSecret, type Field, type Values } from "@web/connect/schema";

/** A form the server describes, drawn for a phone: a connector's install
 *  settings, an account's credentials, an organization's own OAuth app
 *  (`web/schema.ts`, the web's own reading of those schemas).
 *
 *  The web's `connect/fields.tsx`, with the same rules about secrets: a
 *  masked secret is cleared when the box is touched, and put back when it is
 *  left empty, so leaving it alone keeps what the server holds. A secret box
 *  never offers autofill: this is a service's settings, not a sign-in. */
export function Fields({ list, values, problems, onChange, disabled = false }: {
    list: readonly Field[];
    values: Values;
    problems: Record<string, string>;
    onChange: (name: string, value: unknown) => void;
    disabled?: boolean;
}) {
    return (
        <View style={{ gap: 18 }}>
            {list.map((field) => (
                <View key={field.name} style={{ gap: 6 }}>
                    {field.kind !== "boolean" && field.kind !== "choice" ? (
                        <Text size={13.5} tone={color.ink2}>{field.label}{field.required ? <Text size={13.5} tone={color.ink3}> · needed</Text> : null}</Text>
                    ) : null}
                    <One field={field} value={values[field.name]} disabled={disabled} onChange={onChange} />
                    {problems[field.name] ? <Text size={13} tone={color.amber}>{problems[field.name]}</Text> : null}
                    {field.description ? <Text size={13} tone={color.ink3} style={{ lineHeight: 18 }}>{field.description}</Text> : null}
                </View>
            ))}
        </View>
    );
}

const box = () => ({
    borderWidth: 1,
    borderColor: color.line2,
    borderRadius: 12,
    paddingHorizontal: 12,
    minHeight: TOUCH + 4,
    fontFamily: font.regular,
    fontSize: 16,
    color: color.ink,
} as const);

function One({ field, value, disabled, onChange }: {
    field: Field;
    value: unknown;
    disabled: boolean;
    onChange: (name: string, value: unknown) => void;
}) {
    /* Whether a stored secret stood here when the form opened: only then
       does leaving the box empty mean "keep it". */
    const [stored] = useState(() => unchangedSecret(value));
    if (field.kind === "boolean") {
        return (
            <View style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 44 }}>
                <Text size={16} style={{ flex: 1 }}>{field.label}</Text>
                <Switch
                    accessibilityLabel={field.label}
                    value={Boolean(value)}
                    disabled={disabled}
                    onValueChange={(next) => onChange(field.name, next)}
                    trackColor={{ false: color.track, true: color.ink }}
                    thumbColor={value ? color.paper : color.ink3}
                />
            </View>
        );
    }
    if (field.kind === "choice") {
        const choices = (field.options ?? []).map((option) => ({ value: option, label: option }));
        return (
            <View style={{ borderTopWidth: 1, borderTopColor: color.line }}>
                <ChoiceRow label={field.label + (field.required ? " · needed" : "")} choices={choices} value={value ? String(value) : null} onPick={(next) => onChange(field.name, next)} disabled={disabled} />
            </View>
        );
    }
    if (field.kind === "headers") return <Headers field={field} value={value} disabled={disabled} onChange={onChange} />;
    const masked = unchangedSecret(value);
    const secret = field.kind === "secret";
    return (
        <TextInput
            accessibilityLabel={field.label}
            value={masked ? "" : String(value ?? "")}
            placeholder={masked ? "Set. Type to replace it" : field.placeholder}
            placeholderTextColor={color.ink3}
            editable={!disabled}
            secureTextEntry={secret && !masked}
            autoComplete="off"
            textContentType="none"
            importantForAutofill="no"
            autoCorrect={false}
            autoCapitalize="none"
            keyboardType={field.kind === "number" ? "numeric" : "default"}
            onBlur={() => { if (stored && secret && (value === "" || value === undefined)) onChange(field.name, REDACTED); }}
            onChangeText={(next) => onChange(field.name, next)}
            style={box()}
        />
    );
}

/** A map whose keys the tenant chooses (`extra_headers`, `default_headers`):
 *  pairs, each value a secret. */
function Headers({ field, value, disabled, onChange }: {
    field: Field;
    value: unknown;
    disabled: boolean;
    onChange: (name: string, value: unknown) => void;
}) {
    const pairs = Object.entries((value ?? {}) as Record<string, unknown>);
    const write = (next: [string, unknown][]) => onChange(field.name, Object.fromEntries(next));
    const [stored] = useState(() => new Set(pairs.filter(([, held]) => unchangedSecret(held)).map(([key]) => key)));
    return (
        <View style={{ gap: 8 }}>
            {pairs.map(([key, held], at) => {
                const masked = unchangedSecret(held);
                return (
                    <View key={at} style={{ flexDirection: "row", gap: 8, alignItems: "center" }}>
                        <TextInput
                            accessibilityLabel="Header name"
                            placeholder="X-Example"
                            placeholderTextColor={color.ink3}
                            autoComplete="off"
                            autoCapitalize="none"
                            autoCorrect={false}
                            editable={!disabled}
                            value={key}
                            onChangeText={(next) => write(pairs.map((pair, i) => (i === at ? [next, unchangedSecret(pair[1]) ? "" : pair[1]] : pair)))}
                            style={{ ...box(), flex: 1 }}
                        />
                        <TextInput
                            accessibilityLabel={`Value for ${key || "this header"}`}
                            placeholder={masked ? "Set" : "Value"}
                            placeholderTextColor={color.ink3}
                            secureTextEntry={!masked}
                            autoComplete="off"
                            textContentType="none"
                            importantForAutofill="no"
                            autoCapitalize="none"
                            autoCorrect={false}
                            editable={!disabled}
                            value={masked ? "" : String(held ?? "")}
                            onBlur={() => { if ((held === "" || held === undefined) && stored.has(key)) write(pairs.map((pair, i) => (i === at ? [pair[0], REDACTED] : pair))); }}
                            onChangeText={(next) => write(pairs.map((pair, i) => (i === at ? [pair[0], next] : pair)))}
                            style={{ ...box(), flex: 1 }}
                        />
                        <Press label={`Remove ${key || "this header"}`} disabled={disabled} onPress={() => write(pairs.filter((_, i) => i !== at))} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                            <Icon name="close" size={16} tone={color.ink2} />
                        </Press>
                    </View>
                );
            })}
            <Press label="Add a header" disabled={disabled} onPress={() => write([...pairs, ["", ""]])} style={{ flexDirection: "row", alignItems: "center", gap: 6, minHeight: TOUCH, alignSelf: "flex-start" }}>
                <Icon name="plus" size={15} tone={color.ink2} />
                <Text size={14.5} tone={color.ink2}>Add a header</Text>
            </Press>
        </View>
    );
}
