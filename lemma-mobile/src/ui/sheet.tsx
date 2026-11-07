import type { ReactNode } from "react";
import { Modal, Pressable, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Text } from "./text";
import { color } from "./tokens";

/** A sheet from the bottom over a scrim: a tap outside, or back, closes it.
 *  Its content scrolls itself when it needs to (wrap it in a ScrollView). */
export function Sheet({ open, onClose, title, tall = false, children }: {
    open: boolean;
    onClose: () => void;
    title?: string;
    /** Most of the screen rather than most of its lower half. */
    tall?: boolean;
    children: ReactNode;
}) {
    const insets = useSafeAreaInsets();
    return (
        <Modal visible={open} transparent animationType="fade" onRequestClose={onClose} statusBarTranslucent>
            <Pressable accessibilityLabel="Close" onPress={onClose} style={{ flex: 1, backgroundColor: color.scrim, justifyContent: "flex-end" }}>
                <Pressable onPress={() => undefined} style={{ backgroundColor: color.paper, borderTopLeftRadius: 22, borderTopRightRadius: 22, paddingTop: 10, paddingBottom: insets.bottom + 12, maxHeight: tall ? "90%" : "70%" }}>
                    <View style={{ alignSelf: "center", width: 36, height: 4, borderRadius: 2, backgroundColor: color.line2, marginBottom: 10 }} />
                    {title ? <Text size={13} tone={color.ink3} style={{ paddingHorizontal: 20, paddingBottom: 6 }}>{title}</Text> : null}
                    {children}
                </Pressable>
            </Pressable>
        </Modal>
    );
}
