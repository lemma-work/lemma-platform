import { View } from "react-native";
import { useRouter } from "expo-router";
import { IconButton } from "../../../ui/chrome";
import { Button } from "../../../ui/press";
import { Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { usePendingInstall } from "./hooks";

/** "You were installing Frontdesk." The way back to an install that a
 *  sign-in, a new organization or a closed app dropped on the floor (the
 *  web's `resume-install.tsx`). Drawn where somebody lands without a Lemma
 *  to go to: the New Lemma screen and the first run. Nothing when no install
 *  waits; the screen around it gives it its side margins. */
export function ResumeInstall() {
    const router = useRouter();
    const { held, dismiss } = usePendingInstall();
    if (!held) return null;
    return (
        <View accessibilityRole="summary" style={{ flexDirection: "row", alignItems: "flex-start", gap: 6, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: color.line }}>
            <View style={{ flex: 1, gap: 10, paddingTop: 10 }}>
                <Text size={15.5} style={{ lineHeight: 21 }}>
                    You were installing <Text size={15.5} weight="medium">{held.title}</Text>.
                </Text>
                <View style={{ alignSelf: "flex-start" }}>
                    <Button title="Continue installing" onPress={() => router.push(held.path as never)} />
                </View>
            </View>
            <IconButton name="close" size={18} label={`Not now: stop offering ${held.title}`} onPress={dismiss} />
        </View>
    );
}
