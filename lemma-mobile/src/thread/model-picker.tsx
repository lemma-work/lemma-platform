import { useState } from "react";
import { ActivityIndicator, ScrollView, View } from "react-native";
import { useModelPicker } from "../areas/chat/hooks";
import { Icon } from "../ui/icons";
import { Press } from "../ui/press";
import { Sheet } from "../ui/sheet";
import { Text } from "../ui/text";
import { color } from "../ui/tokens";
import type { Choice } from "./runtimes";

/** The model this chat answers on, beside the paperclip: the web's
 *  `model-picker.tsx`, as a sheet. The first row says what "nothing chosen"
 *  means now; choosing it sends no override at all. The choice is the
 *  chat's, not the Lemma's, and applies from the next message on. Before
 *  the chat exists it is held here and goes up with the create. */
export function ModelPicker({ podId, conversationId, agentId, picked, onPicked }: {
    podId: string;
    conversationId: string | null;
    agentId: string | null;
    picked: Choice | null;
    onPicked: (choice: Choice | null) => void;
}) {
    const [open, setOpen] = useState(false);
    const model = useModelPicker(podId, conversationId, agentId, picked);
    if (model.hidden) return null;
    /* Nothing to choose between: a saved pin is still named, since every
       later turn copies it. */
    if (!model.offered) {
        return model.chosen ? <Text size={13} tone={color.ink2} numberOfLines={1} style={{ paddingHorizontal: 8 }}>{model.chosenLabel}</Text> : null;
    }
    const shown = model.chosen ? model.chosenLabel : model.defaultLabel;
    const choose = (name: string) => {
        setOpen(false);
        const next = name ? { runtimeId: model.runtime?.id ?? "", model: name } : null;
        if (conversationId) model.save(next);
        else onPicked(next);
    };
    const value = model.chosen?.model ?? "";
    return (
        <>
            <Press
                label={`Model: ${shown}. Change`}
                onPress={() => setOpen(true)}
                disabled={model.saving}
                style={{ flexDirection: "row", alignItems: "center", gap: 4, height: 30, paddingHorizontal: 8, maxWidth: 190 }}
            >
                {model.saving ? <ActivityIndicator size="small" color={color.ink3} /> : null}
                <Text size={13} tone={model.failed ? color.amber : color.ink2} numberOfLines={1} style={{ flexShrink: 1 }}>{model.failed ? "Not saved" : shown}</Text>
                <Icon name="down" size={12} width={2} tone={color.ink2} />
            </Press>
            <Sheet open={open} onClose={() => setOpen(false)} title="Model for this chat">
                <ScrollView>
                    {[{ name: "", label: model.defaultLabel }, ...model.models].map((one) => (
                        <Press
                            key={one.name || "default"}
                            role="radio"
                            label={one.label}
                            accessibilityState={{ checked: one.name === value }}
                            onPress={() => choose(one.name)}
                            style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: 52, paddingHorizontal: 20 }}
                        >
                            <Text size={16} style={{ flex: 1 }}>{one.label}</Text>
                            {one.name === value ? <Icon name="check" size={18} width={2} tone={color.ink} /> : null}
                        </Press>
                    ))}
                    <View style={{ paddingHorizontal: 20, paddingTop: 6, paddingBottom: 4 }}>
                        <Text size={12.5} tone={color.ink3}>Applies from the next message on.</Text>
                    </View>
                </ScrollView>
            </Sheet>
        </>
    );
}
