import { Stack } from "expo-router";
import { color } from "../../../../ui/tokens";

/** Home and the whole Needs you list, one stack under the Home tab, so the
 *  tab bar stays while the list is open (as the canvas draws it). */
export default function HomeStack() {
    return <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: color.paper }, animation: "slide_from_right" }} />;
}
