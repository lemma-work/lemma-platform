import type { ReactNode } from "react";
import { ActivityIndicator, View } from "react-native";
import { useOrg } from "../../../data/hooks";
import type { Org } from "../../../data/types";
import { Icon } from "../../../ui/icons";
import { Press } from "../../../ui/press";
import { Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { Reading, Say, SettingsScreen } from "../ui";
import type { Tone } from "./model";

/** The organization, or a plain word that there is none, for each of this
 *  part's screens: models belong to an organization. */
export function WithOrg({ title, note, back, right, children }: {
    title: string;
    note?: string | null;
    back: string;
    right?: ReactNode;
    children: (org: Org) => ReactNode;
}) {
    const { org, pending } = useOrg();
    if (org) return <>{children(org)}</>;
    return (
        <SettingsScreen title={title} note={note} back={back} right={right}>
            {pending ? <Reading /> : <Say>You are not in an organization yet, and models belong to one.</Say>}
        </SettingsScreen>
    );
}

/** A row's state as a small mark at its left edge, beside the words that
 *  say it: amber for a problem, quiet ink for ready, an empty ring for
 *  asleep or retired. */
export function StateDot({ tone }: { tone: Tone }) {
    return (
        <View
            accessibilityElementsHidden
            importantForAccessibility="no-hide-descendants"
            style={{
                width: 8,
                height: 8,
                borderRadius: 4,
                backgroundColor: tone === "warn" ? color.amber : tone === "ok" ? color.ink3 : "transparent",
                borderWidth: tone === "muted" ? 1 : 0,
                borderColor: color.ink3,
            }}
        />
    );
}

/** One of a few, in a list already on screen: a tick on the one held, a
 *  spinner on the one being saved. */
export function PickRow({ label, hint, on, saving, disabled, onPress, last }: {
    label: string;
    hint?: string | null;
    on: boolean;
    saving: boolean;
    disabled: boolean;
    onPress: () => void;
    last: boolean;
}) {
    return (
        <Press
            role="radio"
            label={hint ? `${label}, ${hint}` : label}
            disabled={disabled}
            accessibilityState={{ selected: on, disabled }}
            onPress={onPress}
            style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: hint ? 58 : 50, paddingVertical: 6, borderBottomWidth: last ? 0 : 1, borderBottomColor: color.line, opacity: disabled && !on ? 0.45 : 1 }}
        >
            <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
                <Text size={16} numberOfLines={1}>{label}</Text>
                {hint ? <Text size={13} tone={color.ink3} numberOfLines={2}>{hint}</Text> : null}
            </View>
            {saving ? <ActivityIndicator size="small" color={color.ink3} /> : on ? <Icon name="check" size={18} width={2} /> : null}
        </Press>
    );
}

/** A quiet link in the flow of a screen. */
export function Link({ label, onPress }: { label: string; onPress: () => void }) {
    return (
        <Press role="link" label={label} onPress={onPress} style={{ alignSelf: "flex-start", minHeight: 40, justifyContent: "center" }}>
            <Text size={15} weight="medium">{label}</Text>
        </Press>
    );
}
