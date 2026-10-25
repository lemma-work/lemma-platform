import { useState } from "react";
import { View } from "react-native";
import type { SurfaceSetupAction, SurfaceSetupActionField, SurfaceSetupResponse } from "lemma-sdk";
import { copyText } from "../../../thread/clipboard";
import { Button, Press } from "../../../ui/press";
import { Num, Text } from "../../../ui/text";
import { color, TOUCH } from "../../../ui/tokens";
import { openOutside } from "../connect/round-trip";
import { Group, Say } from "../ui";
import { healthOf } from "./places";

/** Whether a place works, what an administrator still has to approve, and
 *  the steps left (the web's `surface-manage.tsx` `Health` and
 *  `surface-setup.tsx`): each step with its values to copy, a secret shown
 *  only when asked, and the provider's page one tap away. */
export function Health({ setup, pending, failed, checking, onCheck }: {
    setup: SurfaceSetupResponse | undefined;
    pending: boolean;
    failed: boolean;
    checking: boolean;
    onCheck: () => void;
}) {
    const health = healthOf(setup, pending, failed);
    const consent = setup?.admin_consent;
    const dot = health.tone === "waiting" ? color.violet : health.tone === "good" ? color.ink : color.ink3;
    return (
        <>
            <Group
                label="Whether it works"
                right={pending ? null : (
                    <Press label="Check again" disabled={checking} onPress={onCheck} style={{ minHeight: 36, justifyContent: "center", paddingLeft: 12 }}>
                        <Text size={13.5} weight="medium" tone={checking ? color.ink3 : color.ink}>{checking ? "Checking…" : "Check again"}</Text>
                    </Press>
                )}
            >
                <View accessibilityRole="text" accessibilityLiveRegion="polite" style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 44 }}>
                    <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: dot }} />
                    <Text size={15.5} style={{ flex: 1 }}>{health.text}</Text>
                </View>
                {consent?.required && !consent.granted && consent.consent_url ? (
                    <View style={{ gap: 8, paddingTop: 4 }}>
                        <Text size={14} tone={color.ink2} style={{ lineHeight: 20 }}>An administrator there has to approve Lemma before it can answer.</Text>
                        <View style={{ flexDirection: "row" }}>
                            <Button title="Approve it as an administrator" onPress={() => void openOutside(consent.consent_url!)} />
                        </View>
                    </View>
                ) : consent?.granted ? (
                    <Say>An administrator approved it.</Say>
                ) : null}
            </Group>
            {setup?.actions?.map((action) => <SetupAction key={action.key} action={action} />)}
        </>
    );
}

function SetupAction({ action }: { action: SurfaceSetupAction }) {
    return (
        <Group label={action.title}>
            <View style={{ gap: 8 }}>
                {action.description ? <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20 }}>{action.description}</Text> : null}
                {action.steps?.length ? (
                    <View style={{ gap: 6 }}>
                        {action.steps.map((step, index) => (
                            <View key={index} style={{ flexDirection: "row", gap: 8 }}>
                                <Num size={14.5} tone={color.ink3} style={{ minWidth: 16 }}>{index + 1}.</Num>
                                <Text size={14.5} style={{ flex: 1, lineHeight: 20 }}>{step}</Text>
                            </View>
                        ))}
                    </View>
                ) : null}
                {action.fields?.length ? (
                    <View style={{ borderTopWidth: 1, borderTopColor: color.line }}>
                        {action.fields.map((field, index) => <SetupField key={index} field={field} />)}
                    </View>
                ) : null}
                {action.link ? (
                    <View style={{ flexDirection: "row" }}>
                        <Button title={action.link_label || "Open the provider's page"} kind="outline" onPress={() => void openOutside(action.link!)} />
                    </View>
                ) : null}
            </View>
        </Group>
    );
}

/** A value a step asks to be pasted somewhere: its label, the value (dots
 *  for a secret until it is asked for), and Copy. */
function SetupField({ field }: { field: SurfaceSetupActionField }) {
    const [shown, setShown] = useState(false);
    const [copied, setCopied] = useState(false);
    const hidden = field.secret && !shown;
    return (
        <View style={{ paddingVertical: 8, gap: 4, borderBottomWidth: 1, borderBottomColor: color.line }}>
            <Text size={13} tone={color.ink3}>{field.label}</Text>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                <Num size={14} numberOfLines={3} selectable={!hidden} style={{ flex: 1 }}>{hidden ? "••••••••" : field.value}</Num>
                {field.secret ? (
                    <Press label={`${shown ? "Hide" : "Show"} ${field.label}`} onPress={() => setShown(!shown)} style={{ minHeight: TOUCH, justifyContent: "center", paddingHorizontal: 6 }}>
                        <Text size={14} weight="medium">{shown ? "Hide" : "Show"}</Text>
                    </Press>
                ) : null}
                <Press label={`Copy ${field.label}`} onPress={() => { copyText(field.value); setCopied(true); setTimeout(() => setCopied(false), 1600); }} style={{ minHeight: TOUCH, justifyContent: "center", paddingHorizontal: 6 }}>
                    <Text size={14} weight="medium">{copied ? "Copied" : "Copy"}</Text>
                </Press>
            </View>
        </View>
    );
}
