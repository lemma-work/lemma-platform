import { Stack } from "expo-router";
import { LinkReturn } from "../../areas/more/link-return";
import { color } from "../../ui/tokens";

/** Every signed-in screen. The root layout guards this group as a whole, so
 *  a new screen is a new file here and nothing else: no list to keep. Only
 *  screens that open differently from a push are named. */
export default function SignedIn() {
    return (
        <>
        <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: color.paper }, animation: "slide_from_right" }}>
            <Stack.Screen name="(tabs)" />
            {/* A Lemma opened from its drawer: its face flies on, so the page fades in under it rather than sliding. */}
            <Stack.Screen name="lemma/[podId]/index" options={{ animation: "fade" }} />
            <Stack.Screen name="new-chat/[podId]" options={{ animation: "slide_from_bottom" }} />
            <Stack.Screen name="notices" options={{ animation: "fade" }} />
            <Stack.Screen name="account" options={{ animation: "slide_from_bottom" }} />
        </Stack>
        {/* A link that came in while signed out opens once they are in (area G). */}
        <LinkReturn />
        </>
    );
}
