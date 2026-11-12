import { View } from "react-native";
import { Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { useSetup } from "./hooks";
import { setupSaid } from "./model";

/** Over a new Lemma's first chat, while its template is coming in (the
 *  web's `SetupStrip`): what it brings, and how far that has got. Nothing
 *  for a chat this app did not start a Lemma in, and gone once it is in. */
export function SetupStrip({ podId, name }: { podId: string; name: string }) {
    const setup = useSetup(podId);
    if (!setup) return null;
    return (
        <View accessibilityRole="summary" accessibilityLiveRegion="polite" style={{ paddingHorizontal: 16, paddingVertical: 9, gap: 2, borderBottomWidth: 1, borderBottomColor: color.line, backgroundColor: color.violetWash }}>
            <Text size={13.5} weight="medium" tone={color.violetInk}>Setting up {name}</Text>
            <Text size={13.5} tone={color.ink2} style={{ lineHeight: 19 }}>{setupSaid(name, setup)}</Text>
        </View>
    );
}
